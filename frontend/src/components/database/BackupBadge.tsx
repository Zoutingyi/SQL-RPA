interface BackupBadgeProps {
  backupId?: string | null;
  status: string;
  onRollback?: (backupId: string) => void;
}

const STATUS_MAP: Record<string, { label: string; className: string }> = {
  active: { label: "已备份", className: "ready" },
  rolled_back: { label: "已回滚", className: "" },
  expired: { label: "已过期", className: "" },
  unavailable: { label: "备份不可用", className: "failed" },
};

export function BackupBadge({ backupId, status, onRollback }: BackupBadgeProps) {
  if (!backupId) {
    return <span className="status-badge">无备份</span>;
  }

  const info = STATUS_MAP[status] || { label: status, className: "" };

  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span className={`status-badge ${info.className}`}>{info.label}</span>
      {status === "active" && onRollback && (
        <button
          className="doc-btn danger"
          style={{ fontSize: 10, padding: "2px 8px" }}
          onClick={(event) => {
            event.stopPropagation();
            onRollback(backupId);
          }}
        >
          回滚
        </button>
      )}
    </span>
  );
}
