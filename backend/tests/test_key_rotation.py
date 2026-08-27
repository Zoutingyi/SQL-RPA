"""End-to-end keyring migration coverage for credentials and backup snapshots."""

import json
import uuid
import zlib

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def client(test_rpa_db, init_rag_db):
    from main import app

    app.state.testing = True
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as value:
        yield value
    from db_connector.factory import close_connector
    await close_connector()


@pytest.mark.asyncio
async def test_rotate_encryption_migrates_credentials_and_backups(
    client, tmp_path, monkeypatch,
):
    from config import settings
    from models.database import async_session
    from models.schemas import DbBackup
    from utils.crypto import decrypt_if_needed, encrypt_if_needed

    old_secret, new_secret = "old-test-secret", "new-test-secret"
    env_path = tmp_path / ".env"
    env_path.write_text(
        "API_KEY=" + encrypt_if_needed("api-value", old_secret, "1") + "\n"
        "LLM_API_KEY=" + encrypt_if_needed("llm-value", old_secret, "1") + "\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(settings.model_config, "env_file", str(env_path))
    monkeypatch.setattr(settings, "secret_key", old_secret)
    monkeypatch.setattr(settings, "encryption_key_version", "2")
    monkeypatch.setattr(
        settings, "encryption_keys_json", json.dumps({"1": old_secret, "2": new_secret})
    )

    snapshot = {"columns": ["id", "name"], "rows": [[1, "Alice"]], "primary_keys": ["id"]}
    compressed_hex = zlib.compress(json.dumps(snapshot).encode("utf-8")).hex()
    backup_id = str(uuid.uuid4())
    async with async_session() as session:
        session.add(DbBackup(
            id=backup_id, table_name="users", operation_type="UPDATE",
            condition_sql="id = 1", rollback_sql="legacy",
            data_snapshot=encrypt_if_needed(compressed_hex, old_secret, "1"),
            affected_rows=1,
        ))
        await session.commit()

    response = await client.post("/api/settings/rotate-encryption")
    assert response.status_code == 200
    assert response.json()["active_version"] == "2"
    persisted = env_path.read_text(encoding="utf-8")
    assert "API_KEY=ENC:v2:" in persisted
    assert "LLM_API_KEY=ENC:v2:" in persisted

    async with async_session() as session:
        backup = await session.get(DbBackup, backup_id)
        assert backup.data_snapshot.startswith("ENC:v2:")
        plaintext = decrypt_if_needed(
            backup.data_snapshot, old_secret, {"1": old_secret, "2": new_secret}
        )
    assert json.loads(zlib.decompress(bytes.fromhex(plaintext))) == snapshot
