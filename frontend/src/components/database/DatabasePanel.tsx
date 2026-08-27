import { useState, useEffect, useCallback, useRef } from "react";
import { useDatabaseStore } from "../../stores/databaseStore";
import { SchemaTree } from "./SchemaTree";
import { DataTable } from "./DataTable";
import { SqlEditor } from "./SqlEditor";
import { OperationLog } from "./OperationLog";
import { ReviewDialog } from "./ReviewDialog";
import { ReviewQueue } from "./ReviewQueue";
import { getTableData, previewOperation, submitReview, getReview, approveReview, rejectReview, recoverExecutionRecords, reviewStateAction } from "../../api/database";
import { useToastStore } from "../../stores/toastStore";
import { useAuthStore } from "../../stores/authStore";
import type { DbColumn, ReviewTask } from "../../types";
import { ApiError, formatApiError } from "../../api/client";
import { useOrganizationStore } from "../../stores/organizationStore";

type Tab = "browse" | "query" | "review" | "history";

export function DatabasePanel() {
  const { tables, selectedTable, columns, loading, loadTables, selectTable } = useDatabaseStore();
  const addToast = useToastStore((s) => s.addToast);
  const currentUser = useAuthStore((s) => s.user);
  const currentContext = useOrganizationStore((s) => s.currentContext);
  const effectiveRole = currentContext ? currentContext.role : currentUser?.role;

  const [tab, setTab] = useState<Tab>("browse");
  const [expandedTable, setExpandedTable] = useState<string | null>(null);

  // DataTable state
  const [rows, setRows] = useState<unknown[][]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(50);
  const [sort, setSort] = useState<{ column: string; order: "asc" | "desc" } | undefined>();

  // SQL execution state
  const [sqlError, setSqlError] = useState<string | null>(null);
  const [sqlLoading, setSqlLoading] = useState(false);

  // Review dialog state
  const [reviewOpen, setReviewOpen] = useState(false);
  const [reviewTask, setReviewTask] = useState<ReviewTask | null>(null);
  const [reviewRefreshToken, setReviewRefreshToken] = useState(0);
  const submissionKeys = useRef(new Map<string, string>());

  // Query result state (from SqlEditor)
  const [queryColumns, setQueryColumns] = useState<string[]>([]);
  const [queryRows, setQueryRows] = useState<unknown[][]>([]);
  const [queryTotal, setQueryTotal] = useState(0);

  useEffect(() => { loadTables(); }, [loadTables]);

  // Load table data when selected table / pagination / sort changes
  const loadData = useCallback(async () => {
    if (!selectedTable) return;
    try {
      const result = await getTableData(
        selectedTable, page, pageSize, sort?.column || "", sort?.order || "asc"
      );
      setRows(result.rows);
      setTotal(result.total);
    } catch {
      addToast({ type: "error", message: "加载数据失败" });
    }
  }, [selectedTable, page, pageSize, sort, addToast]);

  useEffect(() => { loadData(); }, [loadData]);

  const handleSelectTable = async (name: string) => {
    await selectTable(name);
    setExpandedTable(name);
    setPage(1);
    setSort(undefined);
  };

  const handleSort = useCallback((col: string, order: "asc" | "desc") => {
    setSort({ column: col, order });
    setPage(1);
  }, []);

  // SQL Editor: execute SELECT directly, show results below editor
  const handleExecute = useCallback(async (sql: string) => {
    setSqlLoading(true);
    setSqlError(null);
    try {
      const result = await previewOperation(sql);
      setQueryColumns(result.columns || []);
      setQueryRows(result.preview_rows || []);
      setQueryTotal(result.affected_rows || 0);
    } catch (e: any) {
      const detail = typeof e === "string" ? e : e?.detail || e?.message || "执行失败";
      setSqlError(detail);
    } finally {
      setSqlLoading(false);
    }
  }, []);

  // SQL Editor: submit for preview + review
  const handlePreview = useCallback(async (sql: string) => {
    setSqlLoading(true);
    setSqlError(null);
    try {
      const preview = await previewOperation(sql);
      let idempotencyKey = submissionKeys.current.get(sql);
      if (!idempotencyKey) {
        idempotencyKey = crypto.randomUUID();
        submissionKeys.current.set(sql, idempotencyKey);
      }
      const review = await submitReview(sql, "", idempotencyKey);
      setReviewTask({ ...preview, ...review } as ReviewTask);
      setReviewOpen(true);
    } catch (e: any) {
      const detail = typeof e === "string" ? e : e?.detail || e?.message || "预览失败";
      setSqlError(detail);
    } finally {
      setSqlLoading(false);
    }
  }, []);

  const handleApprove = useCallback(async (reason: string) => {
    if (!reviewTask?.id) return;
    try {
      const result = await approveReview(reviewTask.id, reason);
      if (result.status === "pending_second_approval") {
        const fresh = await getReview(reviewTask.id);
        setReviewTask(fresh);
        setReviewRefreshToken((t) => t + 1);
        addToast({ type: "info", message: "已记录第一审批人，等待第二名审批人批准" });
        return;
      }
      if (result.status === "executed_record_pending") {
        const fresh = await getReview(reviewTask.id).catch(() => ({ ...reviewTask, status: result.status }));
        setReviewTask(fresh as ReviewTask);
        setReviewRefreshToken((t) => t + 1);
        addToast({ type: "error", message: "业务 SQL 已执行，审核/审计记录待恢复；请勿重复执行" });
        return;
      }
      submissionKeys.current.delete(reviewTask.sql);
      setReviewTask({
        ...reviewTask,
        status: result.status,
        affected_rows: result.affected_rows ?? reviewTask.affected_rows,
        backup_id: result.backup_id ?? reviewTask.backup_id,
        execution_result: result.execution_result ?? reviewTask.execution_result,
      });
      setReviewRefreshToken((t) => t + 1);
      addToast({ type: "success", message: "操作已执行，可核对 Before/After 结果后关闭" });
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        const fresh = await getReview(reviewTask.id);
        setReviewTask(fresh);
        setReviewRefreshToken((t) => t + 1);
        addToast({ type: "info", message: "任务状态已变化，已刷新为最新状态" });
        return;
      }
      throw error;
    }
  }, [reviewTask, addToast]);

  const handleReject = useCallback(async () => {
    if (!reviewTask?.id) return;
    try {
      await rejectReview(reviewTask.id);
      setReviewOpen(false);
      setReviewTask(null);
      setReviewRefreshToken((t) => t + 1);
      addToast({ type: "info", message: "操作已拒绝" });
    } catch (e: any) {
      if (e instanceof ApiError && e.status === 409) {
        const fresh = await getReview(reviewTask.id);
        setReviewTask(fresh);
        setReviewRefreshToken((t) => t + 1);
        addToast({ type: "info", message: "任务状态已变化，已刷新为最新状态" });
        return;
      }
      const detail = typeof e === "string" ? e : e?.detail || e?.message || "拒绝失败";
      addToast({ type: "error", message: detail });
    }
  }, [reviewTask, addToast]);

  const handleClose = useCallback(() => {
    setReviewOpen(false);
    setReviewTask(null);
  }, []);

  const handleRecover = useCallback(async () => {
    if (!reviewTask?.id) return;
    try {
      await recoverExecutionRecords(reviewTask.id);
      const fresh = await getReview(reviewTask.id);
      setReviewTask(fresh);
      setReviewRefreshToken((t) => t + 1);
      addToast({ type: "success", message: "审核/审计记录已恢复，业务 SQL 未重复执行" });
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        const fresh = await getReview(reviewTask.id);
        setReviewTask(fresh);
        setReviewRefreshToken((t) => t + 1);
        addToast({ type: "info", message: "恢复状态已变化，已刷新最新记录" });
        return;
      }
      const detail = formatApiError(error, "恢复审核/审计记录失败");
      addToast({ type: "error", message: detail });
    }
  }, [reviewTask, addToast]);

  const handleOpenReview = useCallback((task: ReviewTask) => {
    setReviewTask(task);
    setReviewOpen(true);
  }, []);

  const handleReviewAction = useCallback(async (action: "expire" | "revoke" | "escalate" | "transfer", assignedTo: string, reason: string) => {
    if (!reviewTask) return;
    try {
      const fresh = await reviewStateAction(reviewTask.id, action, assignedTo, reason);
      setReviewTask(fresh);
      setReviewRefreshToken((token) => token + 1);
      addToast({ type: "success", message: "审批状态已更新" });
    } catch (error) { addToast({ type: "error", message: formatApiError(error, "审批状态更新失败") }); }
  }, [reviewTask, addToast]);

  return (
    <div className="db-layout">
      <SchemaTree
        tables={tables}
        selectedTable={selectedTable}
        expandedTable={expandedTable}
        columns={columns as DbColumn[]}
        onSelectTable={handleSelectTable}
        onToggleExpand={(name) => setExpandedTable(expandedTable === name ? null : name)}
      />

      <div className="db-main">
        <div className="db-tabs">
          <button className={`db-tab ${tab === "browse" ? "active" : ""}`} onClick={() => setTab("browse")}>
            数据浏览
          </button>
          <button className={`db-tab ${tab === "query" ? "active" : ""}`} onClick={() => setTab("query")}>
            SQL 查询
          </button>
          {(effectiveRole === "approver" || effectiveRole === "admin") && (
            <button className={`db-tab ${tab === "review" ? "active" : ""}`} onClick={() => setTab("review")}>
              审核队列
            </button>
          )}
          {(effectiveRole === "approver" || effectiveRole === "admin") && (
            <button className={`db-tab ${tab === "history" ? "active" : ""}`} onClick={() => setTab("history")}>
              操作历史
            </button>
          )}
        </div>

        <div className="db-tab-content">
          {tab === "browse" && (
            selectedTable ? (
              <DataTable
                columns={columns.map((c: DbColumn) => c.name)}
                rows={rows}
                total={total}
                page={page}
                pageSize={pageSize}
                loading={loading}
                sort={sort}
                onPageChange={setPage}
                onSort={handleSort}
              />
            ) : (
              <div className="db-empty-state">
                <p>选择一个数据表浏览数据</p>
              </div>
            )
          )}

          {tab === "query" && (
            <div style={{ padding: 20, display: "flex", flexDirection: "column", flex: 1 }}>
              <SqlEditor
                onExecute={handleExecute}
                onPreview={handlePreview}
                loading={sqlLoading}
                error={sqlError}
                canWrite={effectiveRole === "operator" || effectiveRole === "approver" || effectiveRole === "admin"}
              />
              {queryRows.length > 0 && (
                <div style={{ marginTop: 16 }}>
                  <div className="data-mask-note">查询结果中的敏感字段已由后端脱敏。</div>
                  <div style={{ fontSize: 13, color: "var(--muted)", marginBottom: 8 }}>
                    查询结果 ({queryTotal} 条)
                  </div>
                  <div style={{ overflowX: "auto" }}>
                    <table className="doc-table">
                      <thead>
                        <tr>
                          {queryColumns.map((c) => <th key={c}>{c}</th>)}
                        </tr>
                      </thead>
                      <tbody>
                        {queryRows.map((row, i) => (
                          <tr key={i}>
                            {row.map((cell, j) => (
                              <td key={j}>
                                {cell === null ? <span className="null-value">NULL</span> : String(cell)}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}

          {tab === "review" && (
            <ReviewQueue refreshToken={reviewRefreshToken} onOpen={handleOpenReview} />
          )}

          {tab === "history" && <OperationLog compact />}
        </div>
      </div>

      <ReviewDialog
        open={reviewOpen}
        task={reviewTask}
        onApprove={handleApprove}
        onReject={handleReject}
        onRecover={handleRecover}
        onStateAction={handleReviewAction}
        onClose={handleClose}
      />
    </div>
  );
}
