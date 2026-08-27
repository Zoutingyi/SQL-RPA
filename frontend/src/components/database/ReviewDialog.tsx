import { useState } from "react";
import { createPortal } from "react-dom";
import type { ReviewTask } from "../../types";
import { useAuthStore } from "../../stores/authStore";
import { useOrganizationStore } from "../../stores/organizationStore";

const LEVEL_CONFIG: Record<string, {
  color: string; border: string; icon: string;
  needReason: boolean; needTableVerify: boolean;
}> = {
  SELECT:   { color: "var(--accent)", border: "var(--accent)", icon: "ℹ", needReason: false, needTableVerify: false },
  INSERT:   { color: "var(--accent)", border: "var(--accent)", icon: "ℹ", needReason: false, needTableVerify: false },
  UPDATE:   { color: "var(--warn)",   border: "var(--warn)",   icon: "⚠", needReason: false, needTableVerify: false },
  DELETE:   { color: "#f97316",       border: "#f97316",       icon: "⚠", needReason: true,  needTableVerify: false },
  DROP:     { color: "var(--danger)", border: "var(--danger)", icon: "⛔", needReason: true,  needTableVerify: true },
  TRUNCATE: { color: "var(--danger)", border: "var(--danger)", icon: "⛔", needReason: true,  needTableVerify: true },
  ALTER:    { color: "var(--danger)", border: "var(--danger)", icon: "⛔", needReason: true,  needTableVerify: true },
};

interface ReviewDialogProps {
  open: boolean;
  task: ReviewTask | null;
  onApprove: (reason: string) => Promise<void>;
  onReject: () => Promise<void>;
  onRecover?: () => Promise<void>;
  onStateAction?: (action: "expire" | "revoke" | "escalate" | "transfer", assignedTo: string, reason: string) => Promise<void>;
  onClose: () => void;
}

export function ReviewDialog({ open, task, onApprove, onReject, onRecover, onStateAction, onClose }: ReviewDialogProps) {
  const [reason, setReason] = useState("");
  const [tableVerify, setTableVerify] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [assignedTo, setAssignedTo] = useState("");
  const currentUser = useAuthStore((s) => s.user);
  const currentContext = useOrganizationStore((s) => s.currentContext);

  if (!open || !task) return null;

  const opType = task.operation_type.toUpperCase();
  const cfg = LEVEL_CONFIG[opType] || LEVEL_CONFIG.UPDATE;
  const previewColumns = task.preview_columns ?? task.columns ?? [];
  const score = task.risk_score;
  const riskLabel = score === undefined ? "未知" : score >= 80 ? "极高" : score >= 60 ? "高" : score >= 35 ? "中" : "低";
  const executionResult = task.execution_result;
  const isDevUser = currentUser?.auth_type === "dev";
  const isSelfSubmitted = !!task.submitted_by && !!currentUser && !isDevUser && task.submitted_by === currentUser.id;
  const isPendingSecond =
    task.status === "pending_second_approval" ||
    (task.status === "awaiting_review" && !!task.approved_by);
  const alreadyApproved = isPendingSecond && !isDevUser && task.approved_by === currentUser?.id;
  const isProcessing = task.status === "executing";
  const isRecordPending = task.status === "executed_record_pending";
  const isTerminal = ["completed", "failed", "rejected", "expired", "cancelled"].includes(task.status);
  const effectiveRole = currentContext ? currentContext.role : currentUser?.role;
  const isReviewer = effectiveRole === "approver" || effectiveRole === "admin";
  const assignedElsewhere = !!task.assigned_to && task.assigned_to !== currentUser?.id && effectiveRole !== "admin";
  const canReject =
    !!currentUser &&
    isReviewer &&
    !isSelfSubmitted && !assignedElsewhere && !isProcessing && !isRecordPending && !isTerminal;

  const canApprove = () => {
    if (!isReviewer || isSelfSubmitted || alreadyApproved || assignedElsewhere || isProcessing || isRecordPending || isTerminal) return false;
    if (cfg.needReason && !reason.trim()) return false;
    if (cfg.needTableVerify && tableVerify.trim() !== task.affected_table) return false;
    return true;
  };

  const handleApprove = async () => {
    if (!canApprove() || submitting) return;
    setSubmitting(true);
    try {
      await onApprove(reason);
    } finally {
      setSubmitting(false);
    }
  };

  const handleReject = async () => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await onReject();
    } finally {
      setSubmitting(false);
    }
  };

  return createPortal(
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal review-dialog" onClick={(e) => e.stopPropagation()}
           style={{ borderTop: `3px solid ${cfg.border}` }}>
        <div className="modal-header">
          <div className="modal-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ color: cfg.color, fontSize: 18 }}>{cfg.icon}</span>
            <span>{task.operation_type} — {task.affected_table}</span>
          </div>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <div className="modal-body">
          <div className="review-summary">
            <div><span className="review-label">操作类型</span><span style={{ color: cfg.color, fontWeight: 600 }}>{task.operation_type}</span></div>
            <div><span className="review-label">目标表</span><span>{task.affected_table}</span></div>
            <div><span className="review-label">影响行数</span><span>{task.affected_rows.toLocaleString()} 条</span></div>
            <div><span className="review-label">提交人</span><span>{task.submitted_by || "未知"}</span></div>
            <div><span className="review-label">第一审批人</span><span>{task.first_approver_id || (isPendingSecond ? task.approved_by : "") || "待审批"}</span></div>
            {task.first_approved_at && (
              <div><span className="review-label">第一审批时间</span><span>{new Date(task.first_approved_at).toLocaleString("zh-CN")}</span></div>
            )}
            {task.second_approver_id && (
              <div><span className="review-label">第二审批人</span><span>{task.second_approver_id}</span></div>
            )}
            {task.second_approved_at && (
              <div><span className="review-label">第二审批时间</span><span>{new Date(task.second_approved_at).toLocaleString("zh-CN")}</span></div>
            )}
            <div><span className="review-label">备份状态</span>
              <span style={{ color: task.has_backup ? "var(--success)" : "var(--muted)" }}>
                {task.has_backup ? "✅ 已生成回滚 SQL" : "— 无需备份"}
              </span>
            </div>
          </div>

          <div className={`risk-score-card risk-${riskLabel}`}>
            <div><span>后端风险评分</span><strong>{score === undefined ? "暂无" : `${score} / 100`}</strong></div>
            {score !== undefined && <div className="risk-score-track"><span style={{ width: `${score}%` }} /></div>}
            <small>{score === undefined ? "该任务未返回风险评分。" : `${riskLabel}风险 · ${task.risk_factors?.join("、") || "无附加风险因素"}`}</small>
          </div>

          {isSelfSubmitted && (
            <div className="review-warning">
              提交人与审批人不能是同一个人，请由其他审批人处理此操作。
            </div>
          )}
          {assignedElsewhere && <div className="review-warning">该任务已指派给 {task.assigned_to}，当前用户不可审批。</div>}

          {isPendingSecond && (
            <div className="review-warning" style={{ borderLeftColor: "var(--warn)" }}>
              该高危操作已记录第一审批人，需由第二名审批人批准后才会执行。
            </div>
          )}

          {isProcessing && (
            <div className="review-warning" style={{ borderLeftColor: "var(--accent)" }}>
              任务正在由其他请求执行，操作按钮已锁定。请等待状态刷新，禁止重复执行。
            </div>
          )}

          {isRecordPending && (
            <div className="rollback-error" role="alert">
              <strong>业务 SQL 已执行，审核/审计记录待恢复</strong>
              <span>数据库可能已经发生修改，禁止再次点击执行。请由管理员恢复记录并核对审计链。</span>
            </div>
          )}

          {isTerminal && (
            <div className="review-warning">当前任务状态为 {task.status}，不可再次审批或执行。</div>
          )}

          <div className="review-section">
            <div className="review-section-title">SQL 语句</div>
            <pre className="review-sql"><code>{task.sql}</code></pre>
          </div>

          {!isTerminal && onStateAction && (
            <div className="review-section">
              <div className="review-section-title">状态操作</div>
              <div className="review-state-actions">
                <input className="review-input" value={assignedTo} onChange={(event) => setAssignedTo(event.target.value)} placeholder="转派/升级目标用户 ID" />
                {(effectiveRole === "admin" || task.submitted_by === currentUser?.id) && <button className="page-btn" onClick={() => onStateAction("revoke", "", reason)}>撤销</button>}
                {isReviewer && <button className="page-btn" onClick={() => onStateAction("escalate", assignedTo, reason)}>升级</button>}
                {effectiveRole === "admin" && <button className="page-btn" disabled={!assignedTo.trim()} onClick={() => onStateAction("transfer", assignedTo, reason)}>转派</button>}
                {effectiveRole === "admin" && <button className="page-btn" onClick={() => onStateAction("expire", "", reason)}>标记过期</button>}
              </div>
            </div>
          )}

          {task.preview_rows && task.preview_rows.length > 0 && (
            <div className="review-section">
              <div className="review-section-title">Before · 当前数据 (前{task.preview_rows.length}条)</div>
              <div style={{ overflowX: "auto" }}>
                <table className="doc-table">
                  <thead>
                    <tr>
                      {previewColumns.map((c) => <th key={c}>{c}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {task.preview_rows.map((row, i) => (
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

          {executionResult ? (
            <div className="review-section">
              <div className="review-section-title">After · 执行后数据</div>
              {executionResult.after.rows.length > 0 ? <div style={{ overflowX: "auto" }}><table className="doc-table"><thead><tr>{executionResult.after.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{executionResult.after.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell === null ? <span className="null-value">NULL</span> : String(cell)}</td>)}</tr>)}</tbody></table></div> : <div className="data-mask-note">执行结果中没有保留行（删除操作或结果为空）。实际影响 {executionResult.affected_rows.toLocaleString()} 行。</div>}
            </div>
          ) : task.status === "completed" ? <div className="data-mask-note">执行结果正在加载或尚未写入。</div> : null}

          <div className="review-section">
            <div className="review-section-title">审批时间线</div>
            <div className="audit-timeline">
              <div className="audit-timeline-item done"><span /> <div><strong>已提交审核</strong><small>{task.created_at ? new Date(task.created_at).toLocaleString("zh-CN") : "时间未知"} · {task.submitted_by || "未知用户"}</small></div></div>
              {task.first_approver_id && <div className="audit-timeline-item done"><span /> <div><strong>第一人已批准</strong><small>{task.first_approved_at ? new Date(task.first_approved_at).toLocaleString("zh-CN") : "时间未知"} · {task.first_approver_id}</small></div></div>}
              {task.second_approver_id && <div className="audit-timeline-item done"><span /> <div><strong>第二人已批准</strong><small>{task.second_approved_at ? new Date(task.second_approved_at).toLocaleString("zh-CN") : "时间未知"} · {task.second_approver_id}</small></div></div>}
              <div className={`audit-timeline-item ${isTerminal || isRecordPending ? "done" : "current"}`}><span /> <div><strong>当前状态：{task.status}</strong><small>{isRecordPending ? "业务已执行，等待内部记录恢复" : isPendingSecond ? "等待不同的第二审批人" : isProcessing ? "正在执行" : "等待处理"}</small></div></div>
            </div>
          </div>

          {task.safety_message && opType !== "SELECT" && (
            <div className="review-warning" style={{ borderLeftColor: cfg.border }}>
              {task.safety_message}
            </div>
          )}

          <div className="review-section">
            <div className="review-section-title">
              操作理由 {cfg.needReason && <span style={{ color: "var(--danger)" }}>*必填</span>}
            </div>
            <textarea
              className="review-input"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={cfg.needReason ? "请说明执行此操作的原因..." : "操作理由（可选）"}
              rows={2}
            />
          </div>

          {cfg.needTableVerify && (
            <div className="review-section">
              <div className="review-section-title">
                请输入表名 <code>{task.affected_table}</code> 以确认操作
              </div>
              <input
                className="review-input mono"
                value={tableVerify}
                onChange={(e) => setTableVerify(e.target.value)}
                placeholder={task.affected_table}
              />
            </div>
          )}
        </div>

        <div className="confirm-footer" style={{ padding: "12px 20px 16px" }}>
          <button className="confirm-cancel" onClick={onClose} disabled={submitting}>关闭</button>
          {canReject && (
            <button className="confirm-danger" onClick={handleReject} disabled={submitting}>
              {submitting ? "处理中..." : "拒绝"}
            </button>
          )}
          {isRecordPending && effectiveRole === "admin" && onRecover && (
            <button className="confirm-primary" onClick={async () => {
              if (submitting) return;
              setSubmitting(true);
              try { await onRecover(); } finally { setSubmitting(false); }
            }} disabled={submitting}>
              {submitting ? "恢复中..." : "恢复审核/审计记录"}
            </button>
          )}
          <button
            className="confirm-primary"
            style={{
              background: cfg.color, borderColor: cfg.color,
              opacity: canApprove() ? 1 : 0.45,
            }}
            disabled={!canApprove() || submitting}
            onClick={handleApprove}
          >
            {submitting ? "处理中..." : isPendingSecond ? "批准并执行" : "确认执行"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
