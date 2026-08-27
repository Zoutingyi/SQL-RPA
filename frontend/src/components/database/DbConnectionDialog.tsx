import { useState, useEffect, useCallback } from "react";
import { createPortal } from "react-dom";
import { getDbStatus, reconnectDb } from "../../api/database";
import { useToastStore } from "../../stores/toastStore";
import type { DbStatus } from "../../types";

interface Props {
  onStatusChange?: (status: DbStatus) => void;
}

const ERROR_ICONS: Record<string, string> = {
  not_implemented: "🔧",
  unsupported_type: "⚠️",
  file_not_found: "📂",
  permission_denied: "🔒",
  connection_failed: "🔌",
  auth_failed: "🔑",
  unknown: "❌",
};

const ERROR_TITLES: Record<string, string> = {
  not_implemented: "数据库类型暂不支持",
  unsupported_type: "不支持的数据库类型",
  file_not_found: "数据库文件未找到",
  permission_denied: "数据库访问被拒绝",
  connection_failed: "无法连接到数据库",
  auth_failed: "数据库认证失败",
  unknown: "数据库连接异常",
};

export function DbConnectionDialog({ onStatusChange }: Props) {
  const [status, setStatus] = useState<DbStatus | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [checked, setChecked] = useState(false);
  const addToast = useToastStore((s) => s.addToast);

  const check = useCallback(async () => {
    try {
      const s = await getDbStatus();
      setStatus(s);
      onStatusChange?.(s);
      if (!s.connected) {
        setShowModal(true);
      } else if (checked) {
        // Only show success toast on reconnect, not on initial load
        addToast({ type: "success", message: `数据库连接正常 — ${s.db_type}, ${s.table_count} 张表` });
      }
    } catch {
      // Network error calling the status API itself
      setStatus({ connected: false, db_type: "", db_name: "", table_count: 0, error: { error_type: "connection_failed", message: "无法访问后端服务", suggestion: "请确认后端服务已启动并可正常访问" } });
      setShowModal(true);
    } finally {
      setChecked(true);
    }
  }, [checked, addToast, onStatusChange]);

  useEffect(() => {
    check();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleReconnect = async () => {
    setReconnecting(true);
    try {
      const s = await reconnectDb();
      setStatus(s);
      onStatusChange?.(s);
      if (s.connected) {
        setShowModal(false);
        addToast({ type: "success", message: `数据库连接成功 — ${s.db_type}, ${s.table_count} 张表` });
      } else {
        addToast({ type: "error", message: "重连失败，请检查数据库配置" });
      }
    } catch {
      setStatus({ connected: false, db_type: status?.db_type ?? "", db_name: status?.db_name ?? "", table_count: 0, error: { error_type: "connection_failed", message: "重连请求失败", suggestion: "请确认后端服务正常运行" } });
    } finally {
      setReconnecting(false);
    }
  };

  const handleDismiss = () => {
    setShowModal(false);
    // Show a toast so the user knows where to reconnect later
    addToast({ type: "info", message: "可在设置页面或数据库面板中重新连接" });
  };

  if (!showModal || !status || status.connected) return null;

  const error = status.error;
  const errorType = error?.error_type ?? "unknown";
  const icon = ERROR_ICONS[errorType] ?? ERROR_ICONS.unknown;
  const title = ERROR_TITLES[errorType] ?? ERROR_TITLES.unknown;

  return createPortal(
    <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) handleDismiss(); }}>
      <div className="modal" style={{ maxWidth: 480 }}>
        <div className="modal-header">
          <span className="modal-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 24 }}>{icon}</span>
            {title}
          </span>
          <button className="modal-close" onClick={handleDismiss}>×</button>
        </div>
        <div className="modal-body">
          <div style={{ marginBottom: 16 }}>
            <div style={{
              fontSize: 13,
              color: "var(--muted)",
              marginBottom: 6,
              fontWeight: 500,
              letterSpacing: "0.02em",
            }}>
              错误详情
            </div>
            <div style={{
              padding: "10px 14px",
              background: "var(--danger-dim)",
              borderLeft: "3px solid var(--danger)",
              borderRadius: "0 var(--radius-sm) var(--radius-sm) 0",
              fontSize: 13,
              lineHeight: 1.6,
              color: "var(--danger)",
            }}>
              {error?.message ?? "未知错误"}
            </div>
          </div>

          {error?.suggestion && (
            <div style={{ marginBottom: 16 }}>
              <div style={{
                fontSize: 13,
                color: "var(--muted)",
                marginBottom: 6,
                fontWeight: 500,
                letterSpacing: "0.02em",
              }}>
                建议
              </div>
              <div style={{
                padding: "10px 14px",
                background: "var(--accent-dim)",
                borderLeft: "3px solid var(--accent)",
                borderRadius: "0 var(--radius-sm) var(--radius-sm) 0",
                fontSize: 13,
                lineHeight: 1.6,
                color: "var(--fg)",
              }}>
                {error.suggestion}
              </div>
            </div>
          )}

          {status.db_type && (
            <div style={{
              fontSize: 12,
              color: "var(--muted)",
              fontFamily: "var(--font-mono)",
              padding: "6px 10px",
              background: "var(--overlay-subtle)",
              borderRadius: "var(--radius-sm)",
            }}>
              {status.db_type}://{status.db_name || "(未配置)"}
            </div>
          )}
        </div>
        <div className="confirm-footer">
          <button className="confirm-cancel" onClick={handleDismiss}>
            稍后处理
          </button>
          <button
            className="confirm-primary"
            onClick={handleReconnect}
            disabled={reconnecting}
          >
            {reconnecting ? "连接中..." : "重新连接"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
