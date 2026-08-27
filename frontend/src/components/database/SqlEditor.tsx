import { useState, useCallback, type KeyboardEvent } from "react";
import { SqlIcon } from "../shared/Icons";

const WRITE_OPS = new Set(["INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", "CREATE"]);

interface SqlEditorProps {
  onExecute: (sql: string) => void;
  onPreview: (sql: string) => void;
  loading: boolean;
  error?: string | null;
  canWrite?: boolean;
}

export function SqlEditor({ onExecute, onPreview, loading, error, canWrite = true }: SqlEditorProps) {
  const [sql, setSql] = useState("");
  const [lastAction, setLastAction] = useState<"execute" | "preview" | null>(null);

  const sqlType = (() => {
    const trimmed = sql.trim();
    if (!trimmed) return null;
    const firstWord = trimmed.toUpperCase().split(/\s+/)[0];
    return WRITE_OPS.has(firstWord) ? "write" : "read";
  })();

  const handleSubmit = useCallback(() => {
    const trimmed = sql.trim();
    if (!trimmed) return;
    if (sqlType === "write") {
      if (!canWrite) return;
      setLastAction("preview");
      onPreview(trimmed);
    } else {
      setLastAction("execute");
      onExecute(trimmed);
    }
  }, [sql, sqlType, canWrite, onExecute, onPreview]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit],
  );

  return (
    <div className="sql-editor-wrap">
      <div className={`sql-editor-header ${sqlType === "write" ? "write" : sqlType === "read" ? "read" : ""}`}>
        <SqlIcon size={14} />
        <span>SQL 查询</span>
        {sqlType === "write" && <span className="sql-type-badge write">写操作</span>}
        {sqlType === "read" && <span className="sql-type-badge read">只读</span>}
        {sqlType === "write" && !canWrite && (
          <span className="sql-type-badge write">查看者不可提交写操作</span>
        )}
      </div>
      <textarea
        className="sql-textarea"
        value={sql}
        onChange={(e) => setSql(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={"输入 SQL 语句...\n\n只读查询：SELECT * FROM table_name\n写操作将自动触发审核流程\nCtrl+Enter 提交"}
        rows={6}
      />
      <div className="sql-editor-footer">
        <span className="sql-hint">Ctrl+Enter 提交</span>
        <div className="sql-actions">
          <button
            className="sql-btn execute"
            disabled={!sql.trim() || loading}
            onClick={() => {
              setLastAction("execute");
              onExecute(sql.trim());
            }}
          >
            执行查询
          </button>
          <button
            className="sql-btn preview"
            disabled={!sql.trim() || loading || (sqlType === "write" && !canWrite)}
            onClick={() => {
              setLastAction("preview");
              onPreview(sql.trim());
            }}
          >
            预览影响
          </button>
        </div>
      </div>
      {loading && (
        <div className="sql-status loading">
          正在{lastAction === "preview" ? "预览" : "执行"}...
        </div>
      )}
      {error && <div className="sql-status error">{error}</div>}
    </div>
  );
}
