import { useEffect, useState, useCallback } from "react";
import { listReviews, getReview, batchReview } from "../../api/database";
import { useToastStore } from "../../stores/toastStore";
import type { ReviewTask } from "../../types";

interface ReviewQueueProps {
  refreshToken: number;
  onOpen: (task: ReviewTask) => void;
}

function reviewProgressLabel(task: ReviewTask): string {
  if (
    task.status === "pending_second_approval" ||
    (task.status === "awaiting_review" && !!task.approved_by)
  ) {
    return "待第二审批";
  }
  if (task.status === "awaiting_review") return "待审批";
  if (task.status === "rejected") return "已拒绝";
  if (task.status === "completed") return "已完成";
  if (task.status === "failed") return "失败";
  if (task.status === "executing") return "执行中";
  if (task.status === "executed_record_pending") return "业务已执行·记录待恢复";
  return task.status;
}

export function ReviewQueue({ refreshToken, onOpen }: ReviewQueueProps) {
  const addToast = useToastStore((s) => s.addToast);
  const [tasks, setTasks] = useState<ReviewTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchNote, setBatchNote] = useState("");
  const [batchLoading, setBatchLoading] = useState(false);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const res = await listReviews("awaiting_review,pending_second_approval,executing,executed_record_pending");
      setTasks(res.items || []);
      setSelected((current) => new Set([...current].filter((id) => res.items.some((task) => task.id === id))));
    } catch {
      if (!silent) addToast({ type: "error", message: "加载审核队列失败" });
    } finally {
      if (!silent) setLoading(false);
    }
  }, [addToast]);

  useEffect(() => {
    load();
    const timer = window.setInterval(() => void load(true), 5000);
    return () => window.clearInterval(timer);
  }, [load, refreshToken]);

  const handleOpen = async (id: string) => {
    try {
      const task = await getReview(id);
      onOpen(task);
    } catch {
      addToast({ type: "error", message: "读取审核任务失败" });
    }
  };

  const handleBatch = async (action: "approve" | "reject") => {
    if (selected.size === 0 || batchLoading) return;
    setBatchLoading(true);
    try {
      const result = await batchReview([...selected], action, batchNote.trim());
      addToast({
        type: result.succeeded === result.total ? "success" : "error",
        message: `批量${action === "approve" ? "批准" : "拒绝"}：成功 ${result.succeeded}/${result.total}`,
      });
      setSelected(new Set());
      setBatchNote("");
      await load();
    } catch {
      addToast({ type: "error", message: "批量审批请求失败" });
    } finally {
      setBatchLoading(false);
    }
  };

  return (
    <div className="review-queue" style={{ padding: 16 }}>
      <div className="review-queue-header">
        <div>
          <div style={{ fontSize: 15, fontWeight: 600 }}>审核队列</div>
          <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 2 }}>
            待审批、执行中及记录待恢复的写操作（自动刷新）
          </div>
        </div>
        <button className="page-btn" onClick={() => void load()} disabled={loading}>
          {loading ? "刷新中..." : "刷新"}
        </button>
      </div>

      <div className="batch-review-bar">
        <input className="review-input" value={batchNote} onChange={(event) => setBatchNote(event.target.value)} placeholder="批量审批意见（可选）" />
        <span>已选 {selected.size} 项</span>
        <button className="page-btn" disabled={selected.size === 0 || batchLoading} onClick={() => handleBatch("reject")}>批量拒绝</button>
        <button className="page-btn" disabled={selected.size === 0 || batchLoading} onClick={() => handleBatch("approve")}>{batchLoading ? "处理中..." : "批量批准"}</button>
      </div>

      {loading && tasks.length === 0 ? (
        <div className="log-loading">加载中...</div>
      ) : tasks.length === 0 ? (
        <div className="log-empty">暂无待审批任务</div>
      ) : (
        <div className="doc-table-wrap" style={{ marginTop: 12 }}>
          <table className="doc-table">
            <thead>
              <tr>
                <th><input type="checkbox" aria-label="选择全部审核任务" checked={tasks.length > 0 && selected.size === tasks.length} onChange={(event) => setSelected(event.target.checked ? new Set(tasks.map((task) => task.id)) : new Set())} /></th>
                <th>操作</th>
                <th>表</th>
                <th>影响行数</th>
                <th>提交人</th>
                <th>审批进度</th>
                <th>提交时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((task) => (
                <tr key={task.id}>
                  <td><input type="checkbox" aria-label={`选择审核任务 ${task.id}`} checked={selected.has(task.id)} onChange={(event) => setSelected((current) => { const next = new Set(current); if (event.target.checked) next.add(task.id); else next.delete(task.id); return next; })} /></td>
                  <td>
                    <span className={`op-type-badge ${task.operation_type}`}>
                      {task.operation_type}
                    </span>
                  </td>
                  <td className="doc-meta">{task.affected_table}</td>
                  <td className="doc-meta">{task.affected_rows.toLocaleString()}</td>
                  <td className="doc-meta">{task.submitted_by || "—"}</td>
                  <td>
                    <span className="status-badge processing">
                      {reviewProgressLabel(task)}
                    </span>
                  </td>
                  <td className="doc-meta">
                    {task.created_at
                      ? new Date(task.created_at).toLocaleString("zh-CN", {
                          month: "2-digit",
                          day: "2-digit",
                          hour: "2-digit",
                          minute: "2-digit",
                        })
                      : "—"}
                  </td>
                  <td>
                    <button className="page-btn" onClick={() => handleOpen(task.id)}>
                      打开审核
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
