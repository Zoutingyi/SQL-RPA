"""Local authentication, RBAC, and audit identity helpers."""

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import unicodedata
from contextvars import ContextVar
from dataclasses import dataclass
from collections import defaultdict

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, func

from config import settings
from models.database import async_session
from models.schemas import Membership, Tenant, User, UserRole

PBKDF2_ITERATIONS = 210_000
TOKEN_ISSUER = "sql-rpa"
TOKEN_AUDIENCE = "sql-rpa-frontend"
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCK_SECONDS = 300

_login_failures: dict[str, list[float]] = defaultdict(list)
_login_lock = threading.Lock()
_login_redis = None
_tenant_context: ContextVar[str] = ContextVar("sql_rpa_tenant_id", default="default")

def get_tenant_id() -> str:
    return _tenant_context.get()


def set_tenant_id(tenant_id: str):
    """Set tenant context and return a token suitable for reset_tenant_id()."""
    return _tenant_context.set(tenant_id)


def reset_tenant_id(token) -> None:
    _tenant_context.reset(token)


def _prune_login_failures(username: str, now: float) -> None:
    cutoff = now - LOGIN_LOCK_SECONDS
    _login_failures[username] = [
        ts for ts in _login_failures.get(username, []) if ts >= cutoff
    ]


def _local_login_lock_remaining(username: str, max_attempts: int = LOGIN_MAX_ATTEMPTS) -> float:
    """Return remaining lock seconds for username, or 0 when not locked."""
    now = time.monotonic()
    with _login_lock:
        _prune_login_failures(username, now)
        failures = _login_failures.get(username, [])
        if len(failures) >= max_attempts:
            return LOGIN_LOCK_SECONDS - (now - failures[0])
    return 0.0


def _local_record_login_failure(username: str) -> None:
    now = time.monotonic()
    with _login_lock:
        _prune_login_failures(username, now)
        _login_failures[username].append(now)


def _local_reset_login_failures(username: str) -> None:
    with _login_lock:
        _login_failures.pop(username, None)


async def _get_login_redis():
    global _login_redis
    if _login_redis is None:
        from redis.asyncio import from_url
        _login_redis = from_url(settings.redis_url, decode_responses=True)
    return _login_redis


async def login_lock_remaining(username: str) -> float:
    if not settings.redis_url:
        if settings.app_env.lower() in {"prod", "production"}:
            raise HTTPException(status_code=503, detail="Login protection is unavailable")
        return _local_login_lock_remaining(username)
    key = f"sql_rpa:login_failures:{hashlib.sha256(username.encode()).hexdigest()}"
    try:
        redis = await _get_login_redis()
        value, ttl = await redis.eval(
            "local v=redis.call('GET',KEYS[1]); "
            "if not v then return {0,0} end; "
            "return {tonumber(v),redis.call('PTTL',KEYS[1])}", 1, key)
        return max(0.0, float(ttl) / 1000) if int(value) >= LOGIN_MAX_ATTEMPTS else 0.0
    except Exception:
        # A configured but unavailable Redis gets a stricter local fallback.
        return _local_login_lock_remaining(username, max_attempts=2)


async def record_login_failure(username: str) -> None:
    if not settings.redis_url:
        _local_record_login_failure(username)
        return
    key = f"sql_rpa:login_failures:{hashlib.sha256(username.encode()).hexdigest()}"
    try:
        redis = await _get_login_redis()
        await redis.eval(
            "local v=redis.call('INCR',KEYS[1]); "
            "if v==1 then redis.call('PEXPIRE',KEYS[1],ARGV[1]) end; return v",
            1, key, LOGIN_LOCK_SECONDS * 1000)
    except Exception:
        _local_record_login_failure(username)


async def reset_login_failures(username: str) -> None:
    _local_reset_login_failures(username)
    if settings.redis_url:
        key = f"sql_rpa:login_failures:{hashlib.sha256(username.encode()).hexdigest()}"
        try:
            redis = await _get_login_redis()
            await redis.delete(key)
        except Exception:
            # Successful authentication remains valid; the stricter fallback
            # protects subsequent attempts while Redis is unavailable.
            pass


@dataclass(frozen=True)
class AuthUser:
    id: str
    username: str
    role: str
    tenant_id: str = "default"
    auth_type: str = "jwt"
    company_id: str | None = None
    organization_id: str | None = None
    membership_id: str | None = None
    organization_level: str | None = None
    is_platform_admin: bool = False
    must_change_password: bool = False


def auth_enabled(request: Request) -> bool:
    """Auth is enforced when a global API key exists and tests did not disable it."""
    return bool(settings.api_key) and not getattr(request.app.state, "testing", False)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def normalize_username(username: str) -> str:
    return unicodedata.normalize("NFKC", username.strip()).casefold()


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), expected)
    except Exception:
        return False


def create_access_token(user: User, expires_in: int = 3600) -> str:
    payload = {
        "sub": user.id,
        "username": user.username,
        "role": "platform_admin" if user.is_platform_admin else "unassigned",
        "iss": TOKEN_ISSUER,
        "aud": TOKEN_AUDIENCE,
        "iat": int(time.time()),
        "exp": int(time.time()) + expires_in,
        "jti": secrets.token_hex(16),
        "token_type": "access",
        "token_version": user.token_version,
        "is_platform_admin": user.is_platform_admin,
    }
    encoded = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    signature = _b64url_encode(
        hmac.new(settings.secret_key.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{encoded}.{signature}"


def create_organization_context_token(user: User, context, expires_in: int = 900) -> str:
    """Issue a short-lived token bound to one validated membership context."""
    payload = {
        "sub": user.id, "username": user.username, "role": context.role,
        "iss": TOKEN_ISSUER, "aud": TOKEN_AUDIENCE,
        "iat": int(time.time()), "exp": int(time.time()) + min(expires_in, 900),
        "jti": secrets.token_hex(16), "token_type": "organization_context",
        "company_id": context.company_id, "organization_id": context.organization_id,
        "membership_id": context.membership_id,
        "organization_level": context.organization_level,
        "context_version": context.context_version,
        "token_version": user.token_version,
    }
    encoded = _b64url_encode(json.dumps(
        payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    signature = _b64url_encode(hmac.new(
        settings.secret_key.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def decode_access_token(token: str) -> dict:
    try:
        encoded, signature = token.rsplit(".", 1)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    expected = _b64url_encode(
        hmac.new(settings.secret_key.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid token signature")

    try:
        payload = json.loads(_b64url_decode(encoded))
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid token payload") from exc

    try:
        exp = int(payload.get("exp", 0))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid token expiration") from exc

    if exp < int(time.time()):
        raise HTTPException(status_code=401, detail="Token expired")
    if payload.get("iss") != TOKEN_ISSUER or payload.get("aud") != TOKEN_AUDIENCE:
        raise HTTPException(status_code=401, detail="Invalid token claims")
    return payload


async def _load_user(user_id: str) -> User | None:
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()


_UNASSIGNED_ALLOWED_PATHS = {
    "/api/auth/me",
    "/api/auth/change-password",
    "/api/departments/memberships/me",
    "/api/departments/context/switch",
}
_PLATFORM_WITHOUT_ORG_ALLOWED_PREFIXES = (
    "/api/auth", "/api/tenants", "/api/settings", "/api/metrics",
    "/api/departments/memberships/me", "/api/departments/context/switch",
)


def _enforce_assigned_access(request: Request, user: AuthUser) -> AuthUser:
    """Prevent an unassigned membership from becoming implicit viewer access."""
    if (user.auth_type == "jwt" and not user.is_platform_admin
            and user.role == "unassigned"
            and request.url.path not in _UNASSIGNED_ALLOWED_PATHS):
        raise HTTPException(status_code=403, detail="No permission is assigned")
    return user


async def get_current_user(request: Request) -> AuthUser:
    if not auth_enabled(request):
        requested_tenant = request.headers.get("X-Tenant-ID", "").strip()
        if settings.multi_tenant_enabled and not requested_tenant:
            raise HTTPException(status_code=400, detail="X-Tenant-ID is required in multi-tenant mode")
        tenant_id = requested_tenant or settings.default_tenant_id
        _tenant_context.set(tenant_id)
        return AuthUser(id="dev-user", username="dev", role=UserRole.admin.value,
                        tenant_id=tenant_id,
                        auth_type="dev")

    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.removeprefix("Bearer ").strip()

    # Legacy single API key is accepted as a service identity. This is a
    # compatibility path; production clients should migrate to JWT users.
    if hmac.compare_digest(token, settings.api_key):
        if settings.multi_tenant_enabled:
            raise HTTPException(status_code=401, detail="Global API key is disabled in multi-tenant mode")
        _tenant_context.set(settings.default_tenant_id)
        return AuthUser(id="legacy-api", username="legacy", role=UserRole.admin.value,
                        tenant_id=settings.default_tenant_id, auth_type="api_key")

    payload = decode_access_token(token)
    user = await _load_user(payload.get("sub", ""))
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User is inactive or not found")
    if int(payload.get("token_version", 0)) != user.token_version:
        raise HTTPException(status_code=401, detail="Token has been revoked")
    if user.must_change_password and request.url.path not in {
            "/api/auth/me", "/api/auth/change-password"}:
        raise HTTPException(status_code=403, detail="Password change is required")
    requested_tenant = request.headers.get("X-Tenant-ID", "").strip()
    claimed_organization = str(payload.get("organization_id") or "")
    claimed_membership = str(payload.get("membership_id") or "")
    organization_id = request.headers.get("X-Organization-ID", "").strip() or claimed_organization
    organization_membership_id = (request.headers.get("X-Membership-ID", "").strip()
                                  or claimed_membership)
    if ((claimed_organization and organization_id != claimed_organization) or
            (claimed_membership and organization_membership_id != claimed_membership)):
        raise HTTPException(status_code=403, detail="Organization token context mismatch")
    if bool(organization_id) != bool(organization_membership_id):
        raise HTTPException(status_code=400, detail="X-Organization-ID and X-Membership-ID are both required")
    if organization_id:
        from organization_context import resolve_organization_context, set_organization_context
        async with async_session() as session:
            organization = await resolve_organization_context(
                session, user_id=user.id, organization_id=organization_id,
                membership_id=organization_membership_id)
        if (payload.get("context_version") is not None and
                int(payload["context_version"]) != organization.context_version):
            raise HTTPException(status_code=401, detail="Organization context token is stale")
        if requested_tenant and requested_tenant != organization.organization_id:
            from models.schemas import DomainEvent
            async with async_session() as session:
                session.add(DomainEvent(
                    id=secrets.token_hex(16), tenant_id=organization.organization_id,
                    aggregate_type="security", aggregate_id=user.id,
                    event_type="security.organization_tenant_mismatch",
                    payload={"organization_id": organization.organization_id,
                             "membership_id": organization.membership_id,
                             "supplied_tenant_id": requested_tenant,
                             "request_id": getattr(request.state, "request_id", ""),
                             "source_ip": request.client.host if request.client else None,
                             "result": "denied"},
                ))
                await session.commit()
            raise HTTPException(status_code=403, detail="Organization and legacy tenant context mismatch")
        set_organization_context(organization)
        # Compatibility tenant identity is derived only from the validated
        # organization. Never let an independent client header select data.
        _tenant_context.set(organization.organization_id)
        return _enforce_assigned_access(request, AuthUser(
            id=user.id, username=user.username, role=organization.role,
            tenant_id=organization.organization_id,
            company_id=organization.company_id, organization_id=organization.organization_id,
            membership_id=organization.membership_id,
            organization_level=organization.organization_level,
            is_platform_admin=user.is_platform_admin,
            must_change_password=user.must_change_password,
        ))
    if user.is_platform_admin and not requested_tenant:
        if not request.url.path.startswith(_PLATFORM_WITHOUT_ORG_ALLOWED_PREFIXES):
            raise HTTPException(status_code=403, detail="Organization context is required")
        _tenant_context.set("")
        return AuthUser(
            id=user.id, username=user.username, role=UserRole.admin.value,
            tenant_id="", is_platform_admin=True,
            must_change_password=user.must_change_password,
        )
    if settings.multi_tenant_enabled and not requested_tenant:
        raise HTTPException(status_code=400, detail="Organization context or X-Tenant-ID is required")
    tenant_id = requested_tenant or settings.default_tenant_id
    async with async_session() as session:
        membership = await session.scalar(select(Membership).where(
            Membership.user_id == user.id, Membership.tenant_id == tenant_id,
            Membership.active.is_(True),
        ))
    if settings.multi_tenant_enabled and not membership:
        raise HTTPException(status_code=403, detail="No active membership for requested tenant")
    _tenant_context.set(tenant_id)
    return _enforce_assigned_access(request, AuthUser(
        id=user.id,
        username=user.username,
        role=(membership.role if membership else
              (UserRole.admin.value if user.is_platform_admin else "unassigned")),
        tenant_id=tenant_id,
        is_platform_admin=user.is_platform_admin,
        must_change_password=user.must_change_password,
    ))


async def ensure_default_tenant() -> None:
    """Create only the legacy tenant row; never grant users an implicit membership."""
    async with async_session() as session:
        tenant = await session.get(Tenant, settings.default_tenant_id)
        if not tenant:
            session.add(Tenant(id=settings.default_tenant_id, name="Default", active=True))
        await session.commit()


def require_roles(*roles: str):
    async def dependency(
        request: Request,
        user: AuthUser = Depends(get_current_user),
    ) -> AuthUser:
        if auth_enabled(request) and user.role not in roles and user.role != UserRole.admin.value:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return dependency


async def ensure_default_admin() -> None:
    """Create an initial admin so an authenticated first start is usable."""
    username = os.environ.get("SQL_RPA_ADMIN_USERNAME", "admin").strip() or "admin"
    password = os.environ.get("SQL_RPA_ADMIN_PASSWORD", "admin") or "admin"

    async with async_session() as session:
        if await session.scalar(select(func.count()).select_from(User).where(
                User.is_platform_admin.is_(True))):
            return
        existing = await session.scalar(select(User).where(
            User.username_normalized == normalize_username(username)))
        if existing:
            raise RuntimeError(
                "Platform administrator bootstrap username already belongs to a legacy user. "
                "Confirm and migrate that user explicitly, or configure a different "
                "SQL_RPA_ADMIN_USERNAME; automatic privilege promotion is forbidden."
            )
        session.add(User(
            id=str(secrets.token_hex(16)),
            username=username,
            display_name=username,
            password_hash=hash_password(password),
            role=UserRole.viewer,
            is_platform_admin=True,
            must_change_password=True,
            profile_incomplete=True,
            is_active=True,
        ))
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            # Another worker may have won the unique insert race.
            if not await session.scalar(select(func.count()).select_from(User).where(
                    User.is_platform_admin.is_(True))):
                raise

    if password == "admin" and settings.app_env.lower() in {"prod", "production"}:
        import sys
        print("[sql-rpa] CRITICAL: default platform administrator must change its password.",
              file=sys.stderr, flush=True)
