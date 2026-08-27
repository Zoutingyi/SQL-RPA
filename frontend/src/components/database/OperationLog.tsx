import { Fragment, useState, useEffect, useCallback } from "react";
import { useDatabaseStore } from "../../stores/databaseStore";
import { rollbackOperation } from "../../api/database";
import { BackupBadge } from "./BackupBadge";
import { useToastStore } from "../../stores/toastStore";
import { formatApiError } from "../../api/client";
import type { RollbackResult } from "../../types";

interface OperationLogProps {
  compact?: boolean;
}

export function OperationLog({ compact }: OperationLogProps) {
  const { operations, loading, loadOperations } = useDatabaseStore();
  const addToast = useToastStore((s) => s.addToast);
  const [filterType, setFilterType] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [rollbackLoading, setRollbackLoading] = useState<string | null>(null);
  const [rollbackTarget, setRollbackTarget] = useState<{ backupId: string; operation: typeof operations[number] } | null>(null);
  const [rollbackReason, setRollbackReason] = useState("");
  const [rollbackError, setRollbackError] = useState("");
  const [rollbackOutcomes, setRollbackOutcomes] = useState<Record<string, RollbackResult>>({});

  useEffect(() => { loadOperations(); }, [loadOperations]);

  const handleRollback = useCallback(async () => {
    if (!rollbackTarget || !rollbackReason.trim()) return;
    setRollbackLoading(rollbackTarget.backupId);
    setRollbackError("");
    try {
      const result = await rollbackOperation(rollbackTarget.backupId, rollbackReason.trim());
      setRollbackOutcomes((current) => ({ ...current, [rollbackTarget.backupId]: result }));
      const complete = result.status === "rolled_back" && !!result.reverse_backup_id && result.restored_rows === rollbackTarget.operation.affected_rows;
      addToast({
        type: complete ? "success" : "error",
        message: complete
          ? `回滚成功，恢复 ${result.restored_rows} 行`
          : "恢复结果不完整或反向备份缺失，必须人工核查；不得视为回滚成功",
      });
      if (complete) {
        setRollbackTarget(null);
        setRollbackReason("");
      } else {
        setRollbackError(
          result.error_message ||
          `后端返回 ${result.status}，反向备份 ${result.reverse_backup_id || "缺失"}，恢复 ${result.restored_rows}/${rollbackTarget.operation.affected_rows} 行。`,
        );
      }
      await loadOperations();
    } catch (error) {
      const detail = formatApiError(error, "服务异常，请稍后重试");
      setRollbackError(detail);
      addToast({ type: "error", message: `回滚失败：${detail}` });
    } finally {
      setRollbackLoading(null);
    }
  }, [rollbackTarget, rollbackReason, addToast, loadOperations]);

  const filtered = operations.filter((op) => {
    if (filterType && op.operation_type !== filterType) return false;
    if (filterStatus && op.status !== filterStatus) return false;
    return true;
  });

  const pageSize = 15;
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const paged = filtered.slice((page - 1) * pageSize, page * pageSize);

  const typeOptions = ["DELETE", "INSERT", "UPDATE", "ROLLBACK", "CREATE", "DROP", "TRUNCATE", "ALTER"];
  const statusOptions = ["completed", "failed", "rejected", "awaiting_review"];

  return (
    <div style={{ padding: compact ? 0 : 24 }}>
      <div className="log-filter-bar">
        <select
          className="log-filter-select"
          value={filterType}
          onChange={(e) => { setFilterType(e.target.value); setPage(1); }}
        >
          <option value="">全部类型</option>
          {typeOptions.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select
          className="log-filter-select"
          value={filterStatus}
          onChange={(e) => { setFilterStatus(e.target.value); setPage(1); }}
        >
          <option value="">全部状态</option>
          {statusOptions.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <span className="log-total">{filtered.length} 条记录</span>
      </div>

      {loading ? (
        <div className="log-loading">加载中...</div>
      ) : paged.length === 0 ? (
        <div className="log-empty">
          {operations.length === 0 ? "暂无操作记录" : "无匹配记录"}
        </div>
      ) : (
        <>
          <div style={{ overflowX: "auto" }}>
            <table className="doc-table">
              <thead>
                <tr>
                  <th>操作</th>
                  <th>SQL</th>
                  <th>表</th>
                  <th>行数</th>
                  <th>提交人</th>
                  <th>审批人</th>
                  <th>状态</th>
                  <th>时间</th>
                  <th>备份</th>
                </tr>
              </thead>
              <tbody>
                {paged.map((op) => {
                  const isExpanded = expandedId === op.id;
                  const backupUsable = op.status === "completed" || op.status === "executed_record_pending";
                  return (
                    <Fragment key={op.id}>
                      <tr onClick={() => setExpandedId(isExpanded ? null : op.id)}
                          style={{ cursor: "pointer" }}>
                        <td>
                          <span className={`op-type-badge ${op.operation_type}`}>
                            {op.operation_type}
                          </span>
                        </td>
                        <td>
                          <code className="log-sql-preview">{op.sql_text}</code>
                        </td>
                        <td className="doc-meta">{op.table_name || "—"}</td>
                        <td className="doc-meta">{op.affected_rows.toLocaleString()}</td>
                        <td className="doc-meta">{op.submitted_by || "—"}</td>
                        <td className="doc-meta">{op.approved_by || "—"}</td>
                        <td>
                          <span className={`status-badge ${op.status === "completed" ? "ready" : op.status === "failed" ? "failed" : "processing"}`}>
                            {op.status === "completed" ? "成功" : op.status === "failed" ? "失败" : op.status === "rejected" ? "已拒绝" : op.status === "awaiting_review" ? "待审核" : op.status === "executing" ? "执行中" : op.status === "executed_record_pending" ? "业务已执行·记录待恢复" : op.status}
                          </span>
                        </td>
                        <td className="doc-meta">
                          {op.created_at ? new Date(op.created_at).toLocaleString("zh-CN", {
                            month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
                          }) : "—"}
                        </td>
                        <td>
                          <BackupBadge
                            backupId={op.backup_id}
                            status={backupUsable ? "active" : "unavailable"}
                            onRollback={!backupUsable || rollbackLoading === op.backup_id ? undefined : (backupId) => {
                              setRollbackError("");
                              setRollbackReason("");
                              setRollbackTarget({ backupId, operation: op });
                            }}
                          />
                        </td>
                      </tr>
                      {isExpanded && (
                        <tr key={`${op.id}-expanded`}>
                          <td colSpan={9} style={{ padding: "0 12px 10px" }}>
                            <pre className="log-sql-expanded"><code>{op.sql_text}</code></pre>
                            {op.error_message && <div className="rollback-error" role="alert"><strong>执行记录异常</strong><span>{op.error_message}</span></div>}
                            {op.backup_id && rollbackOutcomes[op.backup_id] && (() => {
                              const outcome = rollbackOutcomes[op.backup_id!];
                              const complete = outcome.status === "rolled_back" && !!outcome.reverse_backup_id && outcome.restored_rows === op.affected_rows;
                              return (
                                <div className={complete ? "rollback-chain success" : "rollback-error"}>
                                  <strong>{complete ? "回滚链路完整" : "回滚结果待核查"}</strong>
                                  <span>原操作：{op.id}</span>
                                  <span>原备份：{outcome.backup_id}</span>
                                  <span>反向备份：{outcome.reverse_backup_id || "未生成"}</span>
                                  <span>恢复结果：{outcome.restored_rows.toLocaleString()} / 预计 {op.affected_rows.toLocaleString()} 行</span>
                                  {outcome.error_message && <span>{outcome.error_message}</span>}
                                </div>
                              );
                            })()}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="db-pagination">
            <span className="pagination-info">{filtered.length} 条</span>
            <div className="pagination-btns">
              <button className="page-btn" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button>
              <span className="page-current">{page} / {totalPages}</span>
              <button className="page-btn" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>下一页</button>
            </div>
          </div>
        </>
      )}
      {rollbackTarget && (
        <div className="modal-overlay" onClick={() => !rollbackLoading && setRollbackTarget(null)}>
          <div className="modal rollback-dialog" onClick={(event) => event.stopPropagation()}>
            <div className="modal-header">
              <div className="modal-title">确认受控回滚</div>
              <button className="modal-close" disabled={!!rollbackLoading} onClick={() => setRollbackTarget(null)}>×</button>
            </div>
            <div className="modal-body">
              <div className="review-warning">回滚会覆盖目标表的当前数据。系统应先创建反向备份，失败时不得继续回滚。</div>
              <div className="review-summary">
                <div><span className="review-label">目标备份</span><code>{rollbackTarget.backupId}</code></div>
                <div><span className="review-label">目标表</span><span>{rollbackTarget.operation.table_name || "—"}</span></div>
                <div><span className="review-label">原操作</span><span>{rollbackTarget.operation.operation_type}</span></div>
                <div><span className="review-label">预计影响行数</span><span>{rollbackTarget.operation.affected_rows.toLocaleString()} 条</span></div>
              </div>
              <div className="review-section">
                <div className="review-section-title">回滚原因 <span style={{ color: "var(--danger)" }}>*必填</span></div>
                <textarea className="review-input" rows={3} value={rollbackReason} onChange={(event) => setRollbackReason(event.target.value)} placeholder="请说明本次回滚原因，便于审计追踪" />
              </div>
              {rollbackError && <div className="rollback-error" role="alert"><strong>回滚未完成</strong><span>{rollbackError}</span><small>数据状态不能视为已恢复，请根据请求详情排查后重试。</small></div>}
            </div>
            <div className="confirm-footer">
              <button className="confirm-cancel" disabled={!!rollbackLoading} onClick={() => setRollbackTarget(null)}>取消</button>
              <button className="confirm-danger" disabled={!!rollbackLoading || !rollbackReason.trim()} onClick={handleRollback}>{rollbackLoading ? "回滚中..." : "确认回滚"}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
