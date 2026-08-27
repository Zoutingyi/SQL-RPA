import json

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── LLM ──
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2
    llm_fallback_model: str = ""
    llm_circuit_failure_threshold: int = 3
    llm_circuit_recovery_seconds: float = 30.0
    model_prices_json: str = "{}"
    prompt_version: str = "agent-system-v1"

    # ── Embedding ──
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_dim: int = 1536

    # ── Qdrant ──
    qdrant_host: str = ""
    qdrant_port: int = 6333
    qdrant_path: str = "./data/qdrant"
    qdrant_collection: str = "rag_chunks"

    # ── SQLite (RAG Agent internal) ──
    database_url: str = "sqlite+aiosqlite:///./data/rag_agent.db"

    # ── RPA Target Database ──
    db_type: str = "sqlite"
    db_host: str = "localhost"
    db_port: int = 3306
    db_user: str = ""
    db_password: str = ""
    db_name: str = ""
    db_pool_size: int = 5
    db_sqlite_path: str = "./data/rpa.db"

    # ── Storage ──
    upload_dir: str = "./data/uploads"

    # ── Agent ──
    max_loop_iterations: int = 10
    max_tool_retries: int = 3
    max_total_time: int = 120
    chunk_size: int = 512
    chunk_overlap: int = 50
    retrieval_top_k: int = 8
    context_budget_ratio: float = 0.8
    max_tool_result_chars: int = 2000

    # ── Retrieval dedup ──
    dedup_enabled: bool = True
    dedup_similarity_threshold: float = 0.85

    # ── Memory ──
    memory_enabled: bool = True
    memory_max_count: int = 100

    # ── Reranker ──
    rerank_enabled: bool = True
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_top_n: int = 16
    hf_endpoint: str = ""

    # ── Web Search ──
    web_search_enabled: bool = True
    web_search_max_results: int = 5
    web_search_proxy: str = ""

    # ── OCR ──
    ocr_enabled: bool = True
    ocr_min_text_length: int = 50       # OCR 结果的最小有效文本长度（字符）
    ocr_fallback_threshold: int = 20    # 页面提取文本少于此值则触发 OCR 回退

    # ── Auth ──
    api_key: str = ""
    password_weak_values: str = "111111,admin,password,password123,12345678,qwerty123"
    four_eyes_affected_rows: int = 1000
    four_eyes_enabled: bool = True
    four_eyes_operation_types: str = "DELETE"
    review_where_required: bool = True
    review_expiry_hours: int = 24
    masking_exclude_columns: str = "id,quantity,amount,count,stock"

    # ── Server ──
    app_env: str = "production"
    secret_key: str = "change-me-in-production"
    redis_url: str = ""
    slow_query_ms: int = 500
    encryption_key_version: str = "1"
    encryption_keys_json: str = ""
    backup_chunk_bytes: int = 524288
    backup_max_snapshot_bytes: int = 104857600
    backup_total_capacity_bytes: int = 1073741824
    backup_retention_days: int = 7
    billing_currency: str = "USD"
    billing_payment_provider: str = "manual"
    billing_webhook_secret: str = ""
    quota_reservation_tokens: int = 4096
    quota_reservation_cost_usd: float = 0.10
    quota_reservation_ttl_seconds: int = 300
    multi_tenant_enabled: bool = False
    default_tenant_id: str = "default"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


import os
import secrets
from pathlib import Path


def _write_env_key(env_path: Path, key: str, value: str) -> None:
    """Write or update a single key-value pair in .env file."""
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
        new_lines = []
        found = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} "):
                new_lines.append(f"{key}={value}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"{key}={value}")
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    else:
        env_path.write_text(f"{key}={value}\n", encoding="utf-8")


def _init_settings() -> Settings:
    """Load config, resolve secret_key, decrypt API keys."""
    s = Settings()

    env_path = Path(s.model_config.get("env_file", ".env"))
    is_production = s.app_env.lower() in {"prod", "production"}

    # ── SECRET_KEY resolution (never written to .env) ──
    # Priority: 1) SQL_RPA_SECRET_KEY env var  2) .env legacy value  3) session-only random
    has_persistent_secret = False

    if s.secret_key == "change-me-in-production":
        env_secret = os.environ.get("SQL_RPA_SECRET_KEY")
        if env_secret:
            s.secret_key = env_secret
            has_persistent_secret = True
        else:
            # Check for legacy SECRET_KEY in .env (written by older versions)
            legacy_key = ""
            if env_path.exists():
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    if line.startswith("SECRET_KEY="):
                        legacy_key = line.split("=", 1)[1].strip()
                        if legacy_key and legacy_key != "change-me-in-production":
                            break
            if legacy_key:
                s.secret_key = legacy_key
                has_persistent_secret = True
            else:
                s.secret_key = "dev-only-stable-key"
                import sys
                print(
                    "[sql-rpa] WARNING: SECRET_KEY not set. Using a stable development-only "
                    "key so local backup snapshots survive restarts. Set "
                    "SQL_RPA_SECRET_KEY before any shared or production deployment.",
                    file=sys.stderr, flush=True,
                )
    else:
        # Loaded from .env by pydantic-settings — persistent
        has_persistent_secret = True

    if is_production and not has_persistent_secret:
        raise RuntimeError(
            "SQL_RPA_SECRET_KEY is required in production. "
            "Set it via the environment or a persistent .env value; "
            "refusing to start with a session-only key."
        )

    # ── API_KEY: auto-generate and persist ──
    from utils.crypto import decrypt_if_needed, encrypt_if_needed

    try:
        keyring = json.loads(s.encryption_keys_json) if s.encryption_keys_json else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError("ENCRYPTION_KEYS_JSON must be a JSON object") from exc
    if not isinstance(keyring, dict):
        raise RuntimeError("ENCRYPTION_KEYS_JSON must be a JSON object")
    keyring = {str(k).removeprefix("v"): str(v) for k, v in keyring.items()}
    keyring.setdefault("1", s.secret_key)
    active_secret = keyring.get(s.encryption_key_version)
    if not active_secret:
        raise RuntimeError("ENCRYPTION_KEY_VERSION is missing from ENCRYPTION_KEYS_JSON")

    if not s.api_key and "API_KEY" not in os.environ:
        if is_production:
            raise RuntimeError(
                "API_KEY is required in production. "
                "Set it via the environment or a persistent .env value."
            )
        if has_persistent_secret:
            s.api_key = secrets.token_urlsafe(32)
            _write_env_key(env_path, "API_KEY", encrypt_if_needed(
                s.api_key, active_secret, s.encryption_key_version
            ))
        else:
            # Development without a persistent encryption key disables auth rather
            # than generating a random key that would make the app unreachable.
            import sys
            s.api_key = ""
            print(
                "[sql-rpa] WARNING: development authentication is disabled because no "
                "persistent SECRET_KEY is configured. Set SQL_RPA_SECRET_KEY and "
                "API_KEY to enable authenticated multi-user mode.",
                file=sys.stderr,
                flush=True,
            )
    else:
        s.api_key = decrypt_if_needed(s.api_key, s.secret_key, keyring)

    # 必须在 transformers 被 import 之前设置（HF_ENDPOINT 影响模型下载源）
    if s.hf_endpoint:
        os.environ["HF_ENDPOINT"] = s.hf_endpoint

    if s.llm_api_key:
        s.llm_api_key = decrypt_if_needed(s.llm_api_key, s.secret_key, keyring)
    if s.embedding_api_key:
        s.embedding_api_key = decrypt_if_needed(s.embedding_api_key, s.secret_key, keyring)
    if s.db_password:
        s.db_password = decrypt_if_needed(s.db_password, s.secret_key, keyring)

    if not is_production:
        import sys
        print(
            f"[sql-rpa] WARNING: APP_ENV is '{s.app_env}'. Development mode may "
            "disable authentication. For any deployment set APP_ENV=production "
            "and provide SQL_RPA_SECRET_KEY / API_KEY explicitly.",
            file=sys.stderr,
            flush=True,
        )

    return s


settings = _init_settings()


def get_encryption_keyring() -> dict[str, str]:
    """Return normalized configured keys, including the legacy v1 key."""
    raw = json.loads(settings.encryption_keys_json) if settings.encryption_keys_json else {}
    ring = {str(k).removeprefix("v"): str(v) for k, v in raw.items()}
    ring.setdefault("1", settings.secret_key)
    return ring


def get_active_encryption_key() -> tuple[str, str]:
    version = settings.encryption_key_version
    secret = get_encryption_keyring().get(version)
    if not secret:
        raise RuntimeError(f"Active encryption key is unavailable: {version}")
    return version, secret
