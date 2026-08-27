interface SortState {
  column: string;
  order: "asc" | "desc";
}

interface DataTableProps {
  columns: string[];
  rows: unknown[][];
  total: number;
  page: number;
  pageSize: number;
  loading: boolean;
  sort?: SortState;
  onPageChange: (page: number) => void;
  onSort?: (column: string, order: "asc" | "desc") => void;
}

export function DataTable({
  columns, rows, total, page, pageSize, loading, sort,
  onPageChange, onSort,
}: DataTableProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const sortIndicator = (col: string) => {
    if (sort?.column !== col) return "";
    return sort.order === "asc" ? " ↑" : " ↓";
  };

  const handleSort = (col: string) => {
    if (!onSort) return;
    if (sort?.column === col) {
      onSort(col, sort.order === "asc" ? "desc" : "asc");
    } else {
      onSort(col, "asc");
    }
  };

  if (loading) {
    return (
      <div className="db-data-area">
        <table className="doc-table">
          <thead>
            <tr>
              {columns.length > 0
                ? columns.map((c) => <th key={c}>{c}</th>)
                : <th>加载中...</th>}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: 5 }).map((_, i) => (
              <tr key={i}>
                {Array.from({ length: columns.length || 3 }).map((_, j) => (
                  <td key={j}><div className="skeleton" style={{ height: 14, width: `${50 + j * 30}px` }} /></td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="db-data-area">
        <div className="db-empty-state">
          <p>表中暂无数据</p>
        </div>
      </div>
    );
  }

  return (
    <div className="db-data-area">
      <div className="data-mask-note">敏感字段已由后端脱敏，前端不接收原文。</div>
      <div className="db-data-table-wrap">
        <table className="doc-table">
          <thead>
            <tr>
              {columns.map((c) => (
                <th
                  key={c}
                  className={onSort ? "sortable" : ""}
                  onClick={() => handleSort(c)}
                >
                  {c}
                  {onSort && <span className="sort-indicator">{sortIndicator(c)}</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                {row.map((cell, j) => (
                  <td key={j}>
                    {cell === null
                      ? <span className="null-value">NULL</span>
                      : String(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="db-pagination">
        <span className="pagination-info">共 {total} 条</span>
        <div className="pagination-btns">
          <button
            className="page-btn"
            disabled={page <= 1}
            onClick={() => onPageChange(page - 1)}
          >
            上一页
          </button>
          <span className="page-current">{page} / {totalPages}</span>
          <button
            className="page-btn"
            disabled={page >= totalPages}
            onClick={() => onPageChange(page + 1)}
          >
            下一页
          </button>
        </div>
      </div>
    </div>
  );
}
