import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from auth import AuthUser, get_current_user, require_roles
from config import settings
from models.database import async_session
from models.schemas import BillingInvoice, BillingInvoiceLine, BillingPayment, LlmUsageLog
from organization_context import get_visible_organization_ids

router = APIRouter(prefix="/api/billing", tags=["billing"])

class InvoiceRequest(BaseModel):
    period_start: datetime
    period_end: datetime
    user_id: str | None = None
    due_days: int = 14

class PaymentRequest(BaseModel):
    provider: str | None = None

def _invoice_dict(row, lines=None):
    result = {"id": row.id, "user_id": row.user_id, "period_start": row.period_start,
              "period_end": row.period_end, "currency": row.currency,
              "subtotal_usd": row.subtotal_usd, "total_usd": row.total_usd,
              "status": row.status, "issued_at": row.issued_at, "due_at": row.due_at,
              "paid_at": row.paid_at}
    if lines is not None:
        result["lines"] = [{"id": line.id, "description": line.description,
            "quantity": line.quantity, "unit_amount_usd": line.unit_amount_usd,
            "amount_usd": line.amount_usd, "metadata": line.metadata_json} for line in lines]
    return result

@router.post("/invoices", status_code=201)
async def create_invoice(body: InvoiceRequest, user: AuthUser = Depends(require_roles("admin"))):
    scope_ids = await get_visible_organization_ids(user.tenant_id)
    if body.period_end <= body.period_start or not 1 <= body.due_days <= 90:
        raise HTTPException(422, "Invalid invoice period or due_days")
    invoice_key = "|".join([
        user.tenant_id, body.user_id or "*", body.period_start.isoformat(), body.period_end.isoformat()
    ])
    async with async_session() as session:
        keyed = await session.scalar(select(BillingInvoice).where(
            BillingInvoice.tenant_id == user.tenant_id,
            BillingInvoice.invoice_key == invoice_key,
        ))
        if keyed:
            return _invoice_dict(keyed)
        existing = await session.scalar(select(BillingInvoice).where(
            BillingInvoice.tenant_id == user.tenant_id,
            BillingInvoice.user_id == body.user_id,
            BillingInvoice.period_start == body.period_start,
            BillingInvoice.period_end == body.period_end,
        ))
        if existing:
            return _invoice_dict(existing)
        query = select(LlmUsageLog.model, func.sum(LlmUsageLog.total_tokens),
                       func.sum(LlmUsageLog.cost_usd)).where(
            LlmUsageLog.tenant_id.in_(scope_ids),
            LlmUsageLog.created_at >= body.period_start,
            LlmUsageLog.created_at < body.period_end,
        ).group_by(LlmUsageLog.model)
        if body.user_id:
            query = query.where(LlmUsageLog.user_id == body.user_id)
        usage = (await session.execute(query)).all()
        total = round(sum(float(cost or 0) for _, _, cost in usage), 8)
        now = datetime.now(timezone.utc)
        invoice = BillingInvoice(id=str(uuid.uuid4()), tenant_id=user.tenant_id,
            invoice_key=invoice_key, user_id=body.user_id,
            period_start=body.period_start, period_end=body.period_end,
            currency=settings.billing_currency.upper(), subtotal_usd=total, total_usd=total,
            status="open", issued_at=now, due_at=now + timedelta(days=body.due_days))
        session.add(invoice)
        for model, tokens, cost in usage:
            amount = round(float(cost or 0), 8)
            session.add(BillingInvoiceLine(id=str(uuid.uuid4()), tenant_id=user.tenant_id,
                invoice_id=invoice.id,
                description=f"LLM usage: {model}", quantity=int(tokens or 0),
                unit_amount_usd=round(amount / max(int(tokens or 0), 1), 12),
                amount_usd=amount, metadata_json={"model": model, "tokens": int(tokens or 0)}))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            winner = await session.scalar(select(BillingInvoice).where(
                BillingInvoice.tenant_id == user.tenant_id,
                BillingInvoice.invoice_key == invoice_key,
            ))
            if not winner:
                raise
            return _invoice_dict(winner)
    return _invoice_dict(invoice)

@router.get("/invoices")
async def list_invoices(user: AuthUser = Depends(get_current_user)):
    scope_ids = await get_visible_organization_ids(user.tenant_id)
    async with async_session() as session:
        query = select(BillingInvoice).where(BillingInvoice.tenant_id.in_(scope_ids))
        if user.role != "admin":
            query = query.where(BillingInvoice.user_id == user.id)
        rows = (await session.execute(query.order_by(BillingInvoice.issued_at.desc()).limit(100))).scalars().all()
    return {"items": [_invoice_dict(row) for row in rows]}

@router.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: str, user: AuthUser = Depends(get_current_user)):
    scope_ids = await get_visible_organization_ids(user.tenant_id)
    async with async_session() as session:
        row = await session.scalar(select(BillingInvoice).where(
            BillingInvoice.id == invoice_id, BillingInvoice.tenant_id.in_(scope_ids)))
        if not row or (user.role != "admin" and row.user_id != user.id):
            raise HTTPException(404, "Invoice not found")
        lines = (await session.execute(select(BillingInvoiceLine).where(
            BillingInvoiceLine.invoice_id == invoice_id,
            BillingInvoiceLine.tenant_id == row.tenant_id))).scalars().all()
    return _invoice_dict(row, lines)

@router.post("/invoices/{invoice_id}/payments", status_code=201)
async def create_payment(invoice_id: str, body: PaymentRequest,
                         idempotency_key: str = Header(..., alias="Idempotency-Key"),
                         user: AuthUser = Depends(get_current_user)):
    if not idempotency_key or len(idempotency_key) > 200:
        raise HTTPException(422, "A valid Idempotency-Key is required")
    scope_ids = await get_visible_organization_ids(user.tenant_id)
    async with async_session() as session:
        invoice = await session.scalar(select(BillingInvoice).where(
            BillingInvoice.id == invoice_id, BillingInvoice.tenant_id.in_(scope_ids)))
        if not invoice or (user.role != "admin" and invoice.user_id != user.id):
            raise HTTPException(404, "Invoice not found")
        scoped_key = f"{invoice.tenant_id}|{idempotency_key}"
        existing = await session.scalar(select(BillingPayment).where(
            BillingPayment.tenant_id == invoice.tenant_id,
            BillingPayment.idempotency_key == scoped_key))
        if existing:
            if existing.invoice_id != invoice_id:
                raise HTTPException(409, "Idempotency-Key belongs to another invoice")
            return {"id": existing.id, "invoice_id": invoice_id, "status": existing.status,
                    "provider": existing.provider, "amount_usd": existing.amount_usd}
        if invoice.status == "paid":
            raise HTTPException(409, "Invoice is already paid")
        invoice_tenant_id = invoice.tenant_id
        payment = BillingPayment(id=str(uuid.uuid4()), tenant_id=invoice.tenant_id,
            invoice_id=invoice_id,
            idempotency_key=scoped_key, provider=body.provider or settings.billing_payment_provider,
            amount_usd=invoice.total_usd, status="pending")
        session.add(payment)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            winner = await session.scalar(select(BillingPayment).where(
                BillingPayment.tenant_id == invoice_tenant_id,
                BillingPayment.idempotency_key == scoped_key,
            ))
            if not winner:
                raise
            if winner.invoice_id != invoice_id:
                raise HTTPException(409, "Idempotency-Key belongs to another invoice")
            payment = winner
    return {"id": payment.id, "invoice_id": invoice_id, "status": payment.status,
            "provider": payment.provider, "amount_usd": payment.amount_usd}

@router.post("/payments/webhook")
async def payment_webhook(request: Request, signature: str = Header("", alias="X-Billing-Signature")):
    if not settings.billing_webhook_secret:
        raise HTTPException(503, "Billing webhook is not configured")
    raw = await request.body()
    expected = hmac.new(settings.billing_webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(401, "Invalid billing signature")
    payload = json.loads(raw)
    reference, tenant_id, status = (payload.get("payment_id"), payload.get("tenant_id"),
                                    payload.get("status"))
    if not tenant_id:
        raise HTTPException(422, "tenant_id is required")
    if status not in {"succeeded", "failed"}:
        raise HTTPException(422, "Unsupported payment status")
    async with async_session() as session:
        payment = await session.scalar(select(BillingPayment).where(
            BillingPayment.id == reference, BillingPayment.tenant_id == tenant_id))
        if not payment:
            raise HTTPException(404, "Payment not found")
        payment.status = status
        payment.provider_reference = payload.get("provider_reference") or payment.provider_reference
        if status == "succeeded":
            invoice = await session.scalar(select(BillingInvoice).where(
                BillingInvoice.id == payment.invoice_id,
                BillingInvoice.tenant_id == tenant_id))
            if not invoice:
                raise HTTPException(404, "Invoice not found")
            invoice.status, invoice.paid_at = "paid", datetime.now(timezone.utc)
        await session.commit()
    return {"ok": True}

@router.get("/payments")
async def list_payments(_: AuthUser = Depends(require_roles("admin"))):
    scope_ids = await get_visible_organization_ids(_.tenant_id)
    async with async_session() as session:
        rows = (await session.execute(select(BillingPayment).where(
            BillingPayment.tenant_id.in_(scope_ids)).order_by(
            BillingPayment.created_at.desc()).limit(100))).scalars().all()
    return {"items": [{"id": row.id, "invoice_id": row.invoice_id,
        "provider": row.provider, "amount_usd": row.amount_usd, "status": row.status,
        "created_at": row.created_at} for row in rows]}
    scope_ids = await get_visible_organization_ids(user.tenant_id)
