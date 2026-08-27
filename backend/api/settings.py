import asyncio
import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import AuthUser, require_roles

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolve_public_host(host: str, port: int) -> None:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HTTPException(status_code=400, detail="Unable to resolve URL hostname") from exc

    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if not _is_public_ip(ip):
            raise HTTPException(status_code=400, detail="Internal or reserved IP addresses are not allowed")


async def _validate_public_url(url: str) -> None:
    """Reject non-http(s) URLs and obvious internal/reserved hosts for SSRF safety."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Only http/https URLs are allowed")

    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=400, detail="URL hostname is required")

    host_lower = host.lower()
    if host_lower == "localhost" or host_lower.endswith(
        (".localhost", ".local", ".internal", ".lan", ".home", ".test")
    ):
        raise HTTPException(status_code=400, detail="Internal hostnames are not allowed")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        await asyncio.to_thread(_resolve_public_host, host_lower, port)
        return

    if not _is_public_ip(ip):
        raise HTTPException(status_code=400, detail="Internal or reserved IP addresses are not allowed")


async def _probe_llm_stream(
    base_url: str,
    api_key: str,
    model: str,
    provider: str,
) -> bool:
    """Send a minimal streaming request to verify stream/stream_options support."""
    import httpx
    from llm.factory import PROVIDER_PRESETS

    url = base_url.rstrip("/") + "/chat/completions"
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "stream": True,
        "max_tokens": 1,
    }
    preset = PROVIDER_PRESETS.get(provider.lower(), {})
    if preset.get("stream_usage"):
        payload["stream_options"] = {"include_usage": True}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code >= 400:
                    return False
                async for chunk in resp.aiter_text():
                    if chunk.strip():
                        return True
    except Exception:
        return False
    return False


# Fields that can be hot-updated without restart
HOT_UPDATE_FIELDS = {
    "web_search_enabled", "rerank_enabled", "retrieval_top_k",
    "web_search_max_results", "dedup_enabled", "memory_enabled",
    "ocr_enabled",
}

# Credential fields that are written to .env and hot-reloaded
CREDENTIAL_FIELDS = {
    "llm_provider", "llm_model", "llm_api_key", "llm_base_url",
    "embedding_provider", "embedding_model", "embedding_api_key", "embedding_base_url",
}

# Mapping from SettingsUpdate field name → .env key name
_FIELD_TO_ENV_KEY = {
    "llm_provider": "LLM_PROVIDER",
    "llm_model": "LLM_MODEL",
    "llm_api_key": "LLM_API_KEY",
    "llm_base_url": "LLM_BASE_URL",
    "embedding_provider": "EMBEDDING_PROVIDER",
    "embedding_model": "EMBEDDING_MODEL",
    "embedding_api_key": "EMBEDDING_API_KEY",
    "embedding_base_url": "EMBEDDING_BASE_URL",
}


class SettingsUpdate(BaseModel):
    # Toggle fields
    web_search_enabled: bool | None = None
    rerank_enabled: bool | None = None
    retrieval_top_k: int | None = None
    web_search_max_results: int | None = None
    dedup_enabled: bool | None = None
    memory_enabled: bool | None = None
    ocr_enabled: bool | None = None

    # LLM credential fields
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None

    # Embedding credential fields
    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_api_key: str | None = None
    embedding_base_url: str | None = None


@router.get("")
async def get_settings():
    from config import settings
    return {
        "llm": {
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "api_key": "***",
            "base_url": settings.llm_base_url,
        },
        "embedding": {
            "provider": settings.embedding_provider,
            "model": settings.embedding_model,
            "api_key": "***",
            "base_url": settings.embedding_base_url,
        },
        "web_search_enabled": settings.web_search_enabled,
        "rerank_enabled": settings.rerank_enabled,
        "retrieval_top_k": settings.retrieval_top_k,
        "web_search_max_results": settings.web_search_max_results,
        "dedup_enabled": settings.dedup_enabled,
        "memory_enabled": settings.memory_enabled,
        "ocr_enabled": settings.ocr_enabled,
    }


@router.put("")
async def update_settings(
    body: SettingsUpdate,
    user: AuthUser = Depends(require_roles("admin")),
):
    """Hot-update toggle settings and persist credential fields to .env."""
    from config import settings, _write_env_key, get_active_encryption_key
    from utils.crypto import encrypt_if_needed

    env_path = Path(settings.model_config.get("env_file", ".env"))
    has_secret = bool(settings.secret_key and settings.secret_key != "change-me-in-production")
    fields = body.model_dump(exclude_none=True)
    updated = []

    for field, value in fields.items():
        if field in HOT_UPDATE_FIELDS and hasattr(settings, field):
            setattr(settings, field, value)
            updated.append(field)

        elif field in CREDENTIAL_FIELDS and hasattr(settings, field):
            env_key = _FIELD_TO_ENV_KEY[field]
            if field.endswith("_api_key") and value:
                if not has_secret:
                    raise HTTPException(
                        status_code=400,
                        detail="Persistent SECRET_KEY is required before saving credentials.",
                    )
                version, active_secret = get_active_encryption_key()
                persisted_value = encrypt_if_needed(value, active_secret, version)
            else:
                persisted_value = value
            _write_env_key(env_path, env_key, persisted_value)
            # Runtime settings always retain plaintext credentials.
            setattr(settings, field, value)
            updated.append(field)

    if not updated:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    return {"status": "saved", "updated": updated}


@router.post("/rotate-encryption")
async def rotate_encryption(
    user: AuthUser = Depends(require_roles("admin")),
):
    """Batch-migrate persisted credentials and backup snapshots to the active key."""
    from config import (
        settings, _write_env_key, get_active_encryption_key, get_encryption_keyring,
    )
    from models.database import async_session
    from models.schemas import DbBackup
    from sqlalchemy import select
    from utils.crypto import encrypt_if_needed, reencrypt
    import zlib

    env_path = Path(settings.model_config.get("env_file", ".env"))
    ring = get_encryption_keyring()
    active_version, active_secret = get_active_encryption_key()
    values: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()

    migrated_credentials = 0
    failures: list[dict] = []
    for env_key in ("API_KEY", "LLM_API_KEY", "EMBEDDING_API_KEY", "DB_PASSWORD"):
        stored = values.get(env_key)
        if not stored:
            continue
        try:
            if stored.startswith("ENC:"):
                migrated = reencrypt(stored, settings.secret_key, active_version, ring)
            else:
                migrated = encrypt_if_needed(stored, active_secret, active_version)
            _write_env_key(env_path, env_key, migrated)
            migrated_credentials += 1
        except Exception:
            failures.append({"type": "credential", "id": env_key})

    migrated_backups = 0
    async with async_session() as session:
        result = await session.execute(select(DbBackup))
        for backup in result.scalars():
            try:
                if backup.data_snapshot.startswith("ENC:"):
                    backup.data_snapshot = reencrypt(
                        backup.data_snapshot, settings.secret_key, active_version, ring
                    )
                else:
                    compressed_hex = zlib.compress(
                        backup.data_snapshot.encode("utf-8")
                    ).hex()
                    backup.data_snapshot = encrypt_if_needed(
                        compressed_hex, active_secret, active_version
                    )
                migrated_backups += 1
            except Exception:
                failures.append({"type": "backup", "id": backup.id})
        await session.commit()

    return {
        "status": "completed", "active_version": active_version,
        "credentials": migrated_credentials, "backups": migrated_backups,
        "failures": failures, "complete": not failures,
    }


class TestConnectionRequest(BaseModel):
    provider: str = ""
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    kind: str = "llm"  # "llm" or "embedding"


@router.post("/test-connection")
async def test_connection(
    body: TestConnectionRequest = TestConnectionRequest(),
    user: AuthUser = Depends(require_roles("admin")),
):
    """Test LLM/embedding connectivity or the database connection."""
    import time
    import httpx
    from config import settings

    # If kind is provided with provider info, test LLM/embedding
    if body.kind in ("llm", "embedding") and body.provider:
        provider = body.provider or ("openai" if body.kind == "llm" else settings.embedding_provider)
        base_url = body.base_url or ("https://api.openai.com/v1" if provider == "openai" else "")
        api_key = body.api_key or ""
        model = body.model or ""

        if not base_url:
            return {"ok": False, "latency_ms": 0, "detail": "Base URL is required"}

        await _validate_public_url(base_url)

        t0 = time.monotonic()
        try:
            url = base_url.rstrip("/") + "/models"
            headers = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=headers)
                latency_ms = round((time.monotonic() - t0) * 1000)
                if resp.status_code in (200, 401, 403):
                    # 200 = success, 401/403 = auth error but endpoint is reachable
                    if resp.status_code == 200:
                        stream_ok = await _probe_llm_stream(
                            base_url, api_key, model, provider
                        )
                        detail = f"Connected ({provider})"
                        if not stream_ok:
                            detail += "; streaming probe failed"
                        return {"ok": True, "latency_ms": latency_ms, "detail": detail}
                    else:
                        return {"ok": False, "latency_ms": latency_ms, "detail": f"Auth failed (status {resp.status_code}). Check API key."}
                else:
                    return {"ok": False, "latency_ms": latency_ms, "detail": f"HTTP {resp.status_code}"}
        except httpx.ConnectError:
            return {"ok": False, "latency_ms": 0, "detail": "Connection refused — check Base URL"}
        except httpx.TimeoutException:
            return {"ok": False, "latency_ms": 0, "detail": "Connection timed out"}
        except Exception as e:
            return {"ok": False, "latency_ms": 0, "detail": str(e)}

    # Default: test database connection
    from db_connector.factory import get_connector
    t0 = time.monotonic()
    try:
        conn = await get_connector()
        if await conn.health_check():
            latency_ms = round((time.monotonic() - t0) * 1000)
            return {"ok": True, "latency_ms": latency_ms, "detail": "Connection successful"}
        else:
            return {"ok": False, "latency_ms": 0, "detail": "Health check failed"}
    except Exception as e:
        return {"ok": False, "latency_ms": 0, "detail": str(e)}
