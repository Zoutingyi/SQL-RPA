"""Tenant-aware target database connector factory."""
import asyncio
from config import settings, get_encryption_keyring
from utils.crypto import decrypt_if_needed
from .base import DatabaseConnector
from .sqlite_impl import SqliteConnector
from .mysql_impl import MySQLConnector

ConnectorKey = tuple[str, str | None, int]
_connectors: dict[ConnectorKey, DatabaseConnector] = {}
_locks: dict[ConnectorKey, asyncio.Lock] = {}


def _identity(tenant_id: str, config_version: int = 1) -> ConnectorKey:
    from organization_context import get_organization_context
    context = get_organization_context()
    if context:
        return context.organization_id, context.membership_id, config_version
    return tenant_id, None, config_version

def _build_connector(db_type: str, *, host: str = "", port: int = 0, user: str = "",
                     password: str = "", database: str = "", pool_size: int = 5) -> DatabaseConnector:
    kind = db_type.lower()
    if kind == "sqlite":
        return SqliteConnector(database)
    if kind == "mysql":
        return MySQLConnector(host=host, port=port, user=user, password=password,
                              database=database, pool_size=pool_size)
    if kind in ("postgres", "postgresql"):
        from .postgres_impl import PostgreSQLConnector
        return PostgreSQLConnector(host=host, port=port, user=user, password=password,
                                   database=database, pool_size=pool_size)
    raise ValueError(f"Unsupported database type: {db_type}")

def create_connector() -> DatabaseConnector:
    """Return a cached connector; database-backed tenant creation is async."""
    from auth import get_tenant_id
    tenant_id = get_tenant_id()
    key = _identity(tenant_id)
    cached = _connectors.get(key)
    if cached is not None:
        return cached
    if settings.multi_tenant_enabled:
        raise RuntimeError("Tenant database connector is not initialized; call get_connector()")
    connector = _build_connector(
        settings.db_type, host=settings.db_host, port=settings.db_port,
        user=settings.db_user, password=settings.db_password,
        database=(settings.db_sqlite_path if settings.db_type.lower() == "sqlite" else settings.db_name),
        pool_size=settings.db_pool_size,
    )
    _connectors[key] = connector
    return connector

async def _load_tenant_config(tenant_id: str):
    from sqlalchemy import select
    from models.database import async_session
    from models.schemas import TenantDatabaseConfig
    async with async_session() as session:
        row = await session.scalar(select(TenantDatabaseConfig).where(
            TenantDatabaseConfig.tenant_id == tenant_id))
    if row is None:
        raise RuntimeError(f"No target database is configured for tenant {tenant_id}")
    return row


async def _create_tenant_connector(row) -> DatabaseConnector:
    password = decrypt_if_needed(row.encrypted_password or "", settings.secret_key,
                                 get_encryption_keyring())
    return _build_connector(row.db_type, host=row.host or "", port=row.port or 0,
                            user=row.username or "", password=password, database=row.database,
                            pool_size=row.pool_size)

async def get_connector() -> DatabaseConnector:
    """Return the connector for the current tenant, connecting it once."""
    from auth import get_tenant_id
    tenant_id = get_tenant_id()
    if not settings.multi_tenant_enabled:
        connector = create_connector()
        if not await connector.health_check():
            await connector.connect()
        return connector
    row = await _load_tenant_config(tenant_id)
    key = _identity(tenant_id, row.config_version)
    connector = _connectors.get(key)
    if connector is not None and await connector.health_check():
        return connector
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        connector = _connectors.get(key)
        if connector is None:
            connector = await _create_tenant_connector(row)
            _connectors[key] = connector
        if not await connector.health_check():
            await connector.connect()
        return connector

async def close_tenant_connector(tenant_id: str) -> None:
    keys = [key for key in _connectors if key[0] == tenant_id]
    for key in keys:
        connector = _connectors.pop(key, None)
        _locks.pop(key, None)
        if connector is None:
            continue
        try:
            await connector.close()
        except Exception:
            pass

async def close_connector() -> None:
    """Close all tenant connectors (application shutdown/test compatibility)."""
    for tenant_id in {key[0] for key in _connectors}:
        await close_tenant_connector(tenant_id)
