"""Audit hash-chain verification used by the API, CLI, and scheduled checks."""

import hashlib
import json
from dataclasses import dataclass, field

from sqlalchemy import select

from models.database import async_session
from models.schemas import DbOperationLog


def audit_entry_hash(log: DbOperationLog) -> str:
    status = log.status.value if hasattr(log.status, "value") else str(log.status)
    canonical = json.dumps({
        "sequence": log.sequence,
        "operation_type": log.operation_type,
        "sql_text": log.sql_text,
        "affected_rows": log.affected_rows,
        "table_name": log.table_name or "",
        "backup_id": log.backup_id,
        "status": status,
        "executed_by": log.executed_by,
        "submitted_by": log.submitted_by,
        "approved_by": log.approved_by,
        "reviewer_note": log.reviewer_note,
        "created_at": log.created_at.isoformat(),
        "prev_hash": log.prev_hash,
    }, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class AuditVerification:
    valid: bool = True
    checked: int = 0
    errors: list[dict] = field(default_factory=list)


async def verify_audit_chain(after_sequence: int = 0) -> AuditVerification:
    """Verify sequence continuity, previous hashes, and entry contents."""
    report = AuditVerification()
    async with async_session() as session:
        result = await session.execute(
            select(DbOperationLog)
            .where(DbOperationLog.sequence > after_sequence)
            .order_by(DbOperationLog.sequence, DbOperationLog.created_at)
        )
        logs = result.scalars().all()

    expected_sequence = after_sequence + 1
    previous_hash = None
    if after_sequence:
        async with async_session() as session:
            result = await session.execute(
                select(DbOperationLog.entry_hash)
                .where(DbOperationLog.sequence == after_sequence)
                .limit(1)
            )
            previous_hash = result.scalar_one_or_none()

    for log in logs:
        report.checked += 1
        if log.sequence != expected_sequence:
            report.errors.append({"id": log.id, "type": "sequence_gap"})
        if log.prev_hash != previous_hash:
            report.errors.append({"id": log.id, "type": "previous_hash_mismatch"})
        if log.entry_hash != audit_entry_hash(log):
            report.errors.append({"id": log.id, "type": "entry_hash_mismatch"})
        expected_sequence = (log.sequence or expected_sequence) + 1
        previous_hash = log.entry_hash

    report.valid = not report.errors
    return report
