from datetime import datetime, timezone, timedelta
from sqlalchemy import (String, Text, DateTime, Enum as SAEnum, Boolean, JSON, Integer,
                        func, UniqueConstraint, CheckConstraint, Index, text)
from sqlalchemy.orm import Mapped, mapped_column, validates
from .database import Base
import enum


# ── Enums ──

class DocStatus(str, enum.Enum):
    uploaded = "uploaded"
    parsing = "parsing"
    chunking = "chunking"
    embedding = "embedding"
    indexing = "indexing"
    ready = "ready"
    failed = "failed"


class OperationStatus(str, enum.Enum):
    pending = "pending"
    previewing = "previewing"
    awaiting_review = "awaiting_review"
    approved = "approved"
    rejected = "rejected"
    executing = "executing"
    completed = "completed"
    failed = "failed"
    executed_record_pending = "executed_record_pending"


class BackupStatus(str, enum.Enum):
    active = "active"
    rolled_back = "rolled_back"
    expired = "expired"


class UserRole(str, enum.Enum):
    viewer = "viewer"
    operator = "operator"
    approver = "approver"
    admin = "admin"


class OrganizationLevel(str, enum.Enum):
    company = "company"
    department = "department"
    group = "group"
    individual = "individual"


class OrganizationUnit(Base):
    __tablename__ = "organization_units"
    __table_args__ = (
        CheckConstraint("level IN ('company','department','group','individual')",
                        name="ck_organization_level"),
        CheckConstraint("depth BETWEEN 1 AND 4", name="ck_organization_depth"),
        CheckConstraint(
            "(level='company' AND depth=1 AND parent_id IS NULL) OR "
            "(level='department' AND depth=2 AND parent_id IS NOT NULL) OR "
            "(level='group' AND depth=3 AND parent_id IS NOT NULL) OR "
            "(level='individual' AND depth=4 AND parent_id IS NOT NULL)",
            name="ck_organization_level_depth_parent",
        ),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_organization_not_self_parent"),
        CheckConstraint("level <> 'company' OR company_id = id", name="ck_company_is_own_boundary"),
        UniqueConstraint("company_id", "parent_id", "name", name="uq_organization_sibling_name"),
        Index("ix_organization_company_path", "company_id", "path"),
        Index("ix_organization_parent_sort", "parent_id", "sort_order"),
        Index("uq_organization_company_name", "name", unique=True,
              sqlite_where=text("level = 'company'"),
              postgresql_where=text("level = 'company'")),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    level: Mapped[OrganizationLevel] = mapped_column(SAEnum(OrganizationLevel), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    company_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    path: Mapped[str] = mapped_column(String(1200), nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    pending_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    context_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc),
                                                  onupdate=lambda: datetime.now(timezone.utc))


class OrganizationMembership(Base):
    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "organization_id", name="uq_user_organization_membership"),
        CheckConstraint("organization_level IN ('company','department','group','individual')",
                        name="ck_membership_organization_level"),
        CheckConstraint("valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from",
                        name="ck_membership_valid_period"),
        Index("ix_membership_user_active", "user_id", "active"),
        Index(
            "uq_membership_active_primary_level", "user_id", "organization_level",
            unique=True,
            sqlite_where=text("active = 1 AND is_primary = 1"),
            postgresql_where=text("active IS TRUE AND is_primary IS TRUE"),
        ),
        Index(
            "uq_membership_active_individual_node", "organization_id", unique=True,
            sqlite_where=text("active = 1 AND organization_level = 'individual'"),
            postgresql_where=text("active IS TRUE AND organization_level = 'individual'"),
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    organization_level: Mapped[OrganizationLevel] = mapped_column(SAEnum(OrganizationLevel), nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False, default="viewer")
    job_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc),
                                                  onupdate=lambda: datetime.now(timezone.utc))


class TenantOrganizationMapping(Base):
    """Auditable compatibility mapping from a legacy tenant to an organization."""
    __tablename__ = "tenant_organization_mappings"
    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, index=True)
    mapping_level: Mapped[str] = mapped_column(String(20), nullable=False, default="company")
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class LegacyMembershipMapping(Base):
    __tablename__ = "legacy_membership_mappings"
    legacy_membership_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_membership_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    mapping_reason: Mapped[str] = mapped_column(String(40), nullable=False, default="same_id")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class OrganizationMigrationRun(Base):
    __tablename__ = "organization_migration_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    phase: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    source_label: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    counts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class OrganizationMigrationIssue(Base):
    __tablename__ = "organization_migration_issues"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    issue_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    source_table: Mapped[str] = mapped_column(String(100), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_tenant_membership"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class TenantDatabaseConfig(Base):
    __tablename__ = "tenant_database_configs"
    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    db_type: Mapped[str] = mapped_column(String(20), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=True)
    port: Mapped[int] = mapped_column(Integer, nullable=True)
    database: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=True)
    encrypted_password: Mapped[str] = mapped_column(Text, nullable=True)
    pool_size: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


# ── RAG Agent Models ──

class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("tenant_id", "file_hash", name="uq_tenant_document_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    owner_id: Mapped[str] = mapped_column(String(100), nullable=False, default="dev-user", index=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size: Mapped[int] = mapped_column(nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[DocStatus] = mapped_column(SAEnum(DocStatus), default=DocStatus.uploaded)
    chunk_count: Mapped[int] = mapped_column(default=0)
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=True)
    embedding_dim: Mapped[int] = mapped_column(nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(100), nullable=False, default="dev-user", index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    title: Mapped[str] = mapped_column(String(200), default="New Chat")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    last_extracted_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=True)
    tool_call_id: Mapped[str] = mapped_column(String(100), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=True)
    tool_args: Mapped[str] = mapped_column(Text, nullable=True)
    sources: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class UserMemory(Base):
    __tablename__ = "user_memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    owner_id: Mapped[str] = mapped_column(String(100), nullable=False, default="dev-user", index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    memory_type: Mapped[str] = mapped_column(String(20), default="fact", nullable=False)
    deprecated: Mapped[bool] = mapped_column(Boolean, default=False)
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=True)
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ChunkVector(Base):
    __tablename__ = "chunk_vectors"
    chunk_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    vector_json: Mapped[str] = mapped_column(Text, nullable=False)
    text_content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    owner_id: Mapped[str] = mapped_column(String(100), nullable=False, default="dev-user", index=True)
    profile_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    memory_ids: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class LlmUsageLog(Base):
    """Token usage captured from LLM streaming responses."""
    __tablename__ = "llm_usage_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=True, index=True)
    request_id: Mapped[str] = mapped_column(String(100), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )


class LlmDegradationEvent(Base):
    __tablename__ = "llm_degradation_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    primary_model: Mapped[str] = mapped_column(String(100), nullable=False)
    fallback_model: Mapped[str] = mapped_column(String(100), nullable=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    error_type: Mapped[str] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class UsageQuota(Base):
    __tablename__ = "usage_quotas"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_tenant_usage_quota"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    monthly_token_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    monthly_cost_limit_usd: Mapped[float] = mapped_column(nullable=False, default=0.0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class UsageQuotaReservation(Base):
    __tablename__ = "usage_quota_reservations"
    __table_args__ = (UniqueConstraint("organization_id", "request_id",
                                      name="uq_org_quota_reservation_request"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    organization_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    membership_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    request_id: Mapped[str] = mapped_column(String(180), nullable=False)
    reserved_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_cost_usd: Mapped[float] = mapped_column(nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    settled_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)


class FrontendTelemetry(Base):
    __tablename__ = "frontend_telemetry"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=True)
    page: Mapped[str] = mapped_column(String(500), nullable=True)
    request_id: Mapped[str] = mapped_column(String(100), nullable=True, index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class ApprovalPolicy(Base):
    __tablename__ = "approval_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    operation_types: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    tables: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    sensitive_columns: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    min_affected_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    required_approvals: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class DomainEvent(Base):
    __tablename__ = "domain_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    aggregate_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    aggregate_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    read_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class NotificationEndpoint(Base):
    __tablename__ = "notification_endpoints"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    notification_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    endpoint_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_retry_at: Mapped[datetime] = mapped_column(DateTime, nullable=True, index=True)
    last_error: Mapped[str] = mapped_column(Text, nullable=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "event_type", "channel", name="uq_notification_preference"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, default="*")
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class BillingInvoice(Base):
    __tablename__ = "billing_invoices"
    __table_args__ = (UniqueConstraint("tenant_id", "invoice_key", name="uq_tenant_invoice_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    invoice_key: Mapped[str] = mapped_column(String(200), nullable=False)
    user_id: Mapped[str] = mapped_column(String(100), nullable=True, index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    subtotal_usd: Mapped[float] = mapped_column(nullable=False, default=0.0)
    total_usd: Mapped[float] = mapped_column(nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    due_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    paid_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)


class BillingInvoiceLine(Base):
    __tablename__ = "billing_invoice_lines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    invoice_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_amount_usd: Mapped[float] = mapped_column(nullable=False, default=0.0)
    amount_usd: Mapped[float] = mapped_column(nullable=False, default=0.0)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class BillingPayment(Base):
    __tablename__ = "billing_payments"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key",
                                      name="uq_tenant_payment_idempotency"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    invoice_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")
    provider_reference: Mapped[str] = mapped_column(String(200), nullable=True, unique=True)
    amount_usd: Mapped[float] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class User(Base):
    """Global login identity. Organization authorization lives in memberships."""
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    username_normalized: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Legacy migration compatibility only. New authorization must use
    # OrganizationMembership.role or is_platform_admin.
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.viewer, nullable=False)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    can_view_full_phone: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    token_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    profile_incomplete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    @validates("username")
    def _sync_normalized_username(self, _key: str, value: str) -> str:
        import unicodedata
        self.username_normalized = unicodedata.normalize("NFKC", value.strip()).casefold()
        return value.strip()


class UserCreateIdempotency(Base):
    """Exact replay record for transactional user creation."""
    __tablename__ = "user_create_idempotency"
    __table_args__ = (
        UniqueConstraint("actor_id", "idempotency_key", name="uq_user_create_actor_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    response_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


# ── RPA Database Models ──

class DbOperationLog(Base):
    """Database operation audit log."""
    __tablename__ = "db_operation_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    operation_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    sql_text: Mapped[str] = mapped_column(Text, nullable=False)
    affected_rows: Mapped[int] = mapped_column(Integer, default=0)
    table_name: Mapped[str] = mapped_column(String(200), nullable=True)
    backup_id: Mapped[str] = mapped_column(String(36), nullable=True, index=True)
    review_id: Mapped[str] = mapped_column(String(36), nullable=True, index=True)
    status: Mapped[OperationStatus] = mapped_column(
        SAEnum(OperationStatus), default=OperationStatus.pending
    )
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    executed_by: Mapped[str] = mapped_column(String(100), nullable=True, default="agent")
    submitted_by: Mapped[str] = mapped_column(String(100), nullable=True)
    approved_by: Mapped[str] = mapped_column(String(100), nullable=True)
    reviewer_note: Mapped[str] = mapped_column(Text, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=True)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class DbReviewTask(Base):
    """Pending database write operation awaiting review/approval."""
    __tablename__ = "db_review_tasks"
    __table_args__ = (UniqueConstraint("organization_id", "idempotency_key",
                                      name="uq_org_review_idempotency"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    organization_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    membership_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(280), nullable=True)
    sql: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=True, default="")
    operation_type: Mapped[str] = mapped_column(String(20), nullable=False)
    affected_table: Mapped[str] = mapped_column(String(200), nullable=True, default="unknown")
    affected_rows: Mapped[int] = mapped_column(Integer, default=0)
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    risk_factors: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    backup_id: Mapped[str] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="awaiting_review", index=True)
    submitted_by: Mapped[str] = mapped_column(String(100), nullable=True)
    approved_by: Mapped[str] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    first_approver_id: Mapped[str] = mapped_column(String(100), nullable=True)
    first_approver_note: Mapped[str] = mapped_column(Text, nullable=True)
    first_approved_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    second_approver_id: Mapped[str] = mapped_column(String(100), nullable=True)
    second_approver_note: Mapped[str] = mapped_column(Text, nullable=True)
    second_approved_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    preview_columns: Mapped[str] = mapped_column(Text, nullable=True)  # JSON
    preview_rows: Mapped[str] = mapped_column(Text, nullable=True)     # JSON
    execution_result: Mapped[dict] = mapped_column(JSON, nullable=True)
    reviewer_note: Mapped[str] = mapped_column(Text, nullable=True)
    assigned_to: Mapped[str] = mapped_column(String(100), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=True, index=True)
    policy_id: Mapped[str] = mapped_column(String(36), nullable=True)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=True)
    required_approvals: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class DbBackup(Base):
    """Data backup snapshot for rollback."""
    __tablename__ = "db_backups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    table_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    operation_type: Mapped[str] = mapped_column(String(20), nullable=False)
    condition_sql: Mapped[str] = mapped_column(Text, nullable=False)
    rollback_sql: Mapped[str] = mapped_column(Text, nullable=False)
    data_snapshot: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    affected_rows: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[BackupStatus] = mapped_column(SAEnum(BackupStatus), default=BackupStatus.active)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    expired_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc) + timedelta(days=7),
    )


class DbBackupChunk(Base):
    __tablename__ = "db_backup_chunks"
    __table_args__ = (UniqueConstraint("backup_id", "chunk_index", name="uq_backup_chunk_index"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    backup_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)


class DbExecutionSaga(Base):
    """Durable cross-database execution/outbox state.

    A non-terminal row is never automatically re-executed: recovery only
    repairs internal review/audit records after target commit.
    """
    __tablename__ = "db_execution_sagas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    review_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, index=True)
    state: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    backup_id: Mapped[str] = mapped_column(String(36), nullable=True)
    affected_rows: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class DbRollbackRecord(Base):
    __tablename__ = "db_rollback_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="default", index=True)
    original_review_id: Mapped[str] = mapped_column(String(36), nullable=True, index=True)
    original_backup_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    reverse_backup_id: Mapped[str] = mapped_column(String(36), nullable=True, index=True)
    audit_log_id: Mapped[str] = mapped_column(String(36), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
