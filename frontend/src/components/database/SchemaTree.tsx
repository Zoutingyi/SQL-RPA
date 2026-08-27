import { TableIcon } from "../shared/Icons";
import type { DbTable, DbColumn } from "../../types";

interface SchemaTreeProps {
  tables: DbTable[];
  selectedTable: string | null;
  expandedTable: string | null;
  columns: DbColumn[];
  onSelectTable: (name: string) => void;
  onToggleExpand: (name: string) => void;
}

export function SchemaTree({
  tables, selectedTable, expandedTable, columns,
  onSelectTable, onToggleExpand,
}: SchemaTreeProps) {
  return (
    <div className="db-sidebar">
      <div className="db-sidebar-header">数据库表</div>
      <div className="db-table-list">
        {tables.length === 0 && (
          <p className="db-empty-hint">暂无数据表</p>
        )}
        {tables.map((t) => {
          const isSelected = selectedTable === t.name;
          const isExpanded = expandedTable === t.name;
          return (
            <div key={t.name}>
              <div
                className={`db-table-item ${isSelected ? "active" : ""}`}
                onClick={() => onSelectTable(t.name)}
              >
                <span
                  className="db-expand-toggle"
                  onClick={(e) => {
                    e.stopPropagation();
                    onToggleExpand(t.name);
                  }}
                >
                  <svg width={10} height={10} viewBox="0 0 24 24"
                       fill="none" stroke="currentColor" strokeWidth={2}
                       strokeLinecap="round" strokeLinejoin="round"
                       style={{ transform: isExpanded ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform 0.12s" }}>
                    <path d="M6 9l6 6 6-6" />
                  </svg>
                </span>
                <TableIcon size={14} />
                <span className="table-name">{t.name}</span>
                <span className="table-count">{t.row_count}</span>
              </div>
              {isExpanded && isSelected && (
                <div className="db-column-list">
                  {columns.length === 0 && (
                    <div className="db-column-item">
                      <span className="col-name" style={{ color: "var(--muted)" }}>加载中...</span>
                    </div>
                  )}
                  {columns.map((col) => (
                    <div key={col.name} className="db-column-item">
                      <span className="col-name">{col.name}</span>
                      <span className="col-type">{col.type}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
