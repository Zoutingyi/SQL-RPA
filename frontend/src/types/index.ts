export interface Document {
  id: string;
  filename: string;
  file_size: number;
  file_type: string;
  status: "uploaded" | "parsing" | "chunking" | "embedding" | "indexing" | "ready" | "failed";
  chunk_count: number;
  error_message?: string;
  created_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  tool_name?: string;
  sources?: string;
}

export type SSEState = "idle" | "connecting" | "streaming" | "waiting_clarify" | "error";

export interface SSEEvent {
  event: string;
  data: unknown;
}

// ── RPA Database Types ──

export interface DbTable {
  name: string;
  schema: string;
  row_count: number;
}

export interface DbColumn {
  name: string;
  type: string;
  nullable: boolean;
  key: string;
  default: string | null;
  extra: string;
}

export interface DbOperation {
  id: string;
  operation_type: string;
  sql_text: string;
  affected_rows: number;
  table_name: string;
  backup_id?: string | null;
  status: string;
  error_message?: string;
  reviewer_note?: string;
  submitted_by?: string;
  approved_by?: string;
  created_at: string;
}

export interface RollbackResult {
  backup_id: string;
  reverse_backup_id: string | null;
  status: "rolled_back" | "failed" | "partial";
  restored_rows: number;
  error_message?: string;
}

export interface DbBackup {
  id: string;
  table_name: string;
  operation_type: string;
  condition_sql: string;
  affected_rows: number;
  status: "active" | "rolled_back" | "expired";
  created_at: string;
  expired_at: string;
}

export interface TableSchema {
  columns: DbColumn[];
  row_count: number;
}

export interface TableDataResponse {
  columns: string[];
  rows: unknown[][];
  total: number;
  page: number;
  page_size: number;
}

export interface PreviewResponse {
  operation_type: string;
  sql: string;
  affected_table: string;
  affected_rows: number;
  columns: string[];
  preview_rows: unknown[][];
  preview_columns?: string[];
  has_backup: boolean;
  backup_id: string | null;
  safety_level: string;
  safety_message: string;
  warnings: string[];
}

export interface ReviewTask extends PreviewResponse {
  id: string;
  status: string;
  reason: string;
  submitted_by?: string;
  approved_by?: string;
  reviewed_at?: string;
  first_approver_id?: string;
  first_approver_note?: string;
  first_approved_at?: string;
  second_approver_id?: string;
  second_approver_note?: string;
  second_approved_at?: string;
  created_at: string;
  risk_score?: number;
  risk_factors?: string[];
  execution_result?: ExecutionResult | null;
  assigned_to?: string;
  expires_at?: string;
  required_approvals?: number;
  policy_id?: string;
  policy_version?: number;
}

export interface ExecutionResult {
  review_id: string;
  operation_type: string;
  affected_rows: number;
  backup_id: string | null;
  before: { columns: string[]; rows: unknown[][] };
  after: { columns: string[]; rows: unknown[][] };
  executed_at: string;
}

export interface DbConnectionError {
  error_type: string;
  message: string;
  suggestion: string;
}

export interface DbStatus {
  connected: boolean;
  db_type: string;
  db_name: string;
  table_count: number;
  error?: DbConnectionError;
}
