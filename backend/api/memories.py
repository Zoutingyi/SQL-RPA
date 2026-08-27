"""Memory management API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request
from auth import AuthUser, get_current_user

from memory.store import MemoryStore
from memory.profile import ProfileManager

router = APIRouter(prefix="/api/memories", tags=["memories"])


@router.get("")
async def list_memories(user: AuthUser = Depends(get_current_user)):
    """List all non-deprecated memories."""
    store = MemoryStore(user.tenant_id, user.id)
    memories = await store.list_memories(include_deprecated=False)
    return {"count": len(memories), "memories": memories}


@router.get("/profile")
async def get_profile(user: AuthUser = Depends(get_current_user)):
    """Get the latest user profile."""
    pm = ProfileManager(user.tenant_id, user.id)
    profile = await pm.get_latest_profile()
    if profile is None:
        return {"profile": None, "message": "No profile generated yet"}
    return {"profile": profile}


@router.post("/profile/generate")
async def generate_profile(user: AuthUser = Depends(get_current_user)):
    """Trigger profile regeneration from current memories."""
    pm = ProfileManager(user.tenant_id, user.id)
    profile_data = await pm.generate_profile()
    if profile_data.get("total_memories", 0) == 0:
        return {"profile": None, "message": "No memories available to build profile"}
    profile_id = await pm.save_profile(profile_data, memory_ids=[])
    latest = await pm.get_latest_profile()
    return {"profile": latest, "message": "Profile regenerated successfully"}


@router.put("/{memory_id}")
async def update_memory(memory_id: str, request: Request,
                        user: AuthUser = Depends(get_current_user)):
    """Update a memory's content and/or deprecated flag."""
    body = await request.json()
    content = body.get("content")
    deprecated = body.get("deprecated")

    if content is None and deprecated is None:
        raise HTTPException(status_code=400, detail="No fields to update")

    store = MemoryStore(user.tenant_id, user.id)
    ok = await store.update_memory(memory_id, content=content, deprecated=deprecated)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"ok": True}


@router.delete("/{memory_id}")
async def delete_memory(memory_id: str, user: AuthUser = Depends(get_current_user)):
    """Delete a single memory by ID."""
    store = MemoryStore(user.tenant_id, user.id)
    ok = await store.delete_memory(memory_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"ok": True}


@router.delete("")
async def clear_all_memories(user: AuthUser = Depends(get_current_user)):
    """Delete all memories."""
    store = MemoryStore(user.tenant_id, user.id)
    count = await store.clear_all()
    return {"ok": True, "deleted_count": count}
