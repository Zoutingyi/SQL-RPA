import hashlib
import uuid
import asyncio
import os

from fastapi import APIRouter, Depends, Request, UploadFile, File, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from models.database import async_session
from models.schemas import Document, DocStatus
from storage.local import LocalStorage
from rag.pipeline import pipeline
from rag.progress import progress_tracker
from config import settings
from auth import AuthUser, get_current_user
from organization_context import get_resource_scope, get_visible_organization_ids

router = APIRouter(prefix="/api/documents", tags=["documents"])

ALLOWED_EXTENSIONS = {".txt", ".md", ".csv", ".pdf", ".docx", ".xlsx"}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB

storage = LocalStorage()


def _validate_file(filename: str, file_size: int) -> None:
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"不支持的文件类型: {ext}。支持: {', '.join(sorted(ALLOWED_EXTENSIONS))}")
    if file_size > MAX_FILE_SIZE:
        raise HTTPException(400, f"文件过大 ({file_size / 1024 / 1024:.1f}MB)，最大 20MB")


@router.get("")
async def list_documents(
    status: str = Query("", description="Filter by status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: AuthUser = Depends(get_current_user),
):
    scope_ids, owner_only = await get_resource_scope(user.tenant_id, user_id=user.id)
    async with async_session() as session:
        base = select(Document).where(Document.tenant_id.in_(scope_ids))
        if owner_only:
            base = base.where(Document.owner_id == user.id)
        count_base = select(Document)
        if status:
            base = base.where(Document.status == status)
            count_base = count_base.where(Document.status == status)

        count_query = select(func.count(Document.id)).where(
            Document.tenant_id.in_(scope_ids))
        if status:
            count_query = count_query.where(Document.status == status)
        total = await session.scalar(count_query)

        offset = (page - 1) * page_size
        rows = await session.execute(
            base.order_by(Document.created_at.desc()).offset(offset).limit(page_size)
        )
        docs = rows.scalars().all()

        return {
            "items": [
                {
                    "id": d.id,
                    "filename": d.filename,
                    "file_size": d.file_size,
                    "file_type": d.file_type,
                    "status": d.status.value if isinstance(d.status, DocStatus) else d.status,
                    "chunk_count": d.chunk_count,
                    "error_message": d.error_message,
                    "created_at": d.created_at.isoformat() if d.created_at else "",
                }
                for d in docs
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }


@router.post("/upload")
async def upload_document(request: Request, file: UploadFile = File(...),
                          user: AuthUser = Depends(get_current_user)):
    if not file.filename:
        raise HTTPException(400, "未选择文件")

    # Check Content-Length header before reading body into memory
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_FILE_SIZE:
        raise HTTPException(400, f"文件过大 ({int(content_length) / 1024 / 1024:.1f}MB)，最大 20MB")

    content = await file.read()
    file_size = len(content)
    _validate_file(file.filename, file_size)

    file_hash = hashlib.sha256(content).hexdigest()

    async with async_session() as session:
        existing = await session.execute(
            select(Document.id).where(Document.file_hash == file_hash,
                                      Document.tenant_id == user.tenant_id)
        )
        if existing.fetchone():
            raise HTTPException(409, "文件已存在（哈希重复）")

        doc_id = uuid.uuid4().hex
        ext = os.path.splitext(file.filename)[1].lower()
        doc = Document(
            id=doc_id,
            tenant_id=user.tenant_id, owner_id=user.id,
            filename=file.filename,
            file_hash=file_hash,
            file_size=file_size,
            file_type=ext.lstrip("."),
            status=DocStatus.uploaded,
        )
        session.add(doc)
        await session.commit()

    # Rewind and save
    await file.seek(0)
    file_path = await storage.save(file, prefix=doc_id)

    # Start async ingestion
    asyncio.create_task(pipeline.ingest(file_path, doc_id))

    return {
        "id": doc_id,
        "filename": file.filename,
        "file_size": file_size,
        "file_type": ext.lstrip("."),
        "status": "uploaded",
        "message": "文件已上传，正在后台处理",
    }


@router.delete("/{document_id}")
async def delete_document(document_id: str, user: AuthUser = Depends(get_current_user)):
    scope_ids = await get_visible_organization_ids(user.tenant_id)
    async with async_session() as session:
        doc = await session.scalar(select(Document).where(
            Document.id == document_id, Document.tenant_id.in_(scope_ids)))
        if not doc:
            raise HTTPException(404, "文档不存在")

        # Clean up vector + FTS5 indexes
        await pipeline.delete(document_id)

        # Delete file from disk (find by checking upload dir)
        for fname in os.listdir(settings.upload_dir):
            if document_id in fname:
                os.remove(os.path.join(settings.upload_dir, fname))
                break

        await session.delete(doc)
        await session.commit()

    return {"ok": True, "message": "文档已删除"}


@router.post("/{document_id}/reprocess")
async def reprocess_document(document_id: str, user: AuthUser = Depends(get_current_user)):
    scope_ids = await get_visible_organization_ids(user.tenant_id)
    async with async_session() as session:
        doc = await session.scalar(select(Document).where(
            Document.id == document_id, Document.tenant_id.in_(scope_ids)))
        if not doc:
            raise HTTPException(404, "文档不存在")

        # Find file on disk
        file_path = ""
        for fname in os.listdir(settings.upload_dir):
            if document_id in fname:
                file_path = os.path.join(settings.upload_dir, fname)
                break

        if not file_path or not os.path.exists(file_path):
            raise HTTPException(404, "文件不存在于磁盘")

        doc.status = DocStatus.uploaded
        doc.error_message = None
        await session.commit()

    asyncio.create_task(pipeline.reprocess(doc_id=document_id, file_path=file_path))

    return {"ok": True, "message": "重新处理已启动"}


@router.get("/{document_id}/progress")
async def document_progress(document_id: str, user: AuthUser = Depends(get_current_user)):
    scope_ids = await get_visible_organization_ids(user.tenant_id)
    async with async_session() as session:
        exists = await session.scalar(select(Document.id).where(
            Document.id == document_id, Document.tenant_id.in_(scope_ids)))
    if not exists:
        raise HTTPException(404, "文档不存在")
    async def event_stream():
        async for event in progress_tracker.subscribe(document_id):
            yield f"data: {event.progress_pct:.0f}|{event.stage}|{event.message}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
