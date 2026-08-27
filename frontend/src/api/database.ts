import { apiGet, apiPost } from "./client";
import type { DbTable, DbOperation, TableSchema, TableDataResponse, PreviewResponse, ReviewTask, DbStatus, RollbackResult, ExecutionResult } from "../types";

export function getDbStatus(): Promise<DbStatus> {
  return apiGet("/api/db_operations/status");
}

export function reconnectDb(): Promise<DbStatus> {
  return apiPost("/api/db_operations/reconnect");
}

export function listTables(): Promise<DbTable[]> {
  return apiGet("/api/db_operations/tables");
}

export function getTableSchema(tableName: string): Promise<TableSchema> {
  return apiGet(`/api/db_operations/tables/${tableName}`);
}

export function getTableData(
  tableName: string, page = 1, pageSize = 50, sort = "", order = "asc"
): Promise<TableDataResponse> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (sort) { params.set("sort", sort); params.set("order", order); }
  return apiGet(`/api/db_operations/tables/${tableName}/data?${params}`);
}

export function previewOperation(sql: string): Promise<PreviewResponse> {
  return apiPost("/api/db_operations/preview", { sql });
}

export function submitReview(sql: string, reason = "", idempotencyKey?: string): Promise<ReviewTask> {
  return apiPost(
    "/api/db_operations/submit-review",
    { sql, reason },
    idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {},
  );
}

export function getReview(reviewId: string): Promise<ReviewTask> {
  return apiGet(`/api/db_operations/review/${reviewId}`);
}

export function listReviews(
  status = ""
): Promise<{ items: ReviewTask[]; total: number }> {
  const params = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiGet(`/api/db_operations/reviews${params}`);
}

export function approveReview(
  reviewId: string, reason = ""
): Promise<{ status: string; affected_rows?: number; backup_id?: string | null; message?: string; execution_result?: ExecutionResult }> {
  return apiPost(`/api/db_operations/review/${reviewId}/approve`, { reviewer_note: reason });
}

export function rejectReview(reviewId: string, note?: string): Promise<unknown> {
  return apiPost(`/api/db_operations/review/${reviewId}/reject`, { reviewer_note: note });
}

export interface BatchReviewResult {
  action: "approve" | "reject"; total: number; succeeded: number;
  items: Array<{ review_id: string; ok: boolean; status_code?: number; error?: string }>;
}

export function batchReview(reviewIds: string[], action: "approve" | "reject", reviewerNote = ""): Promise<BatchReviewResult> {
  return apiPost("/api/db_operations/reviews/batch", { review_ids: reviewIds, action, reviewer_note: reviewerNote });
}

export function getExecutionResult(reviewId: string): Promise<ExecutionResult> {
  return apiGet(`/api/db_operations/review/${reviewId}/execution-result`);
}

export function reviewStateAction(reviewId: string, action: "expire" | "revoke" | "escalate" | "transfer", assignedTo = "", reason = ""): Promise<ReviewTask> {
  return apiPost(`/api/db_operations/review/${reviewId}/actions/${action}`, { assigned_to: assignedTo, reason });
}

export function recoverExecutionRecords(reviewId: string): Promise<{ status: string; review_id: string; replayed: false }> {
  return apiPost(`/api/db_operations/audit/recover/${reviewId}`);
}

export function rollbackOperation(backupId: string, reason: string): Promise<RollbackResult> {
  return apiPost(`/api/db_operations/rollback/${backupId}`, { confirm: true, reason });
}

export function getOperationLogs(): Promise<{ items: DbOperation[]; total: number }> {
  return apiGet("/api/db_operations/logs");
}
