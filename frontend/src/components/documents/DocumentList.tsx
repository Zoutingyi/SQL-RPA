import { useState, useEffect, useCallback, useRef } from "react";
import { listDocuments, uploadDocument, deleteDocument } from "../../api/documents";
import type { Document } from "../../types";
import { formatApiError } from "../../api/client";

const STATUS_LABELS: Record<string, string> = {
  uploaded: "已上传",
  parsing: "解析中",
  chunking: "切分中",
  embedding: "向量化",
  indexing: "索引中",
  ready: "就绪",
  failed: "失败",
};

const STATUS_CLASS: Record<string, string> = {
  uploaded: "processing",
  parsing: "processing",
  chunking: "processing",
  embedding: "processing",
  indexing: "processing",
  ready: "ready",
  failed: "failed",
};

const ALLOWED_TYPES = [".txt", ".md", ".csv", ".pdf", ".docx", ".xlsx"];

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function DocumentList() {
  const [docs, setDocs] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

  const fetchDocs = useCallback(async () => {
    try {
      const data = await listDocuments();
      setDocs(data.items);
      setError(null);
    } catch (error) {
      setError(formatApiError(error, "加载文档列表失败"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDocs();
  }, [fetchDocs]);

  // Poll while any document is processing
  useEffect(() => {
    const hasProcessing = docs.some((d) =>
      ["uploaded", "parsing", "chunking", "embedding", "indexing"].includes(d.status)
    );
    if (hasProcessing && !pollRef.current) {
      pollRef.current = setInterval(fetchDocs, 2000);
    } else if (!hasProcessing && pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = undefined;
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [docs, fetchDocs]);

  const handleUpload = useCallback(
    async (file: File) => {
      const ext = "." + file.name.split(".").pop()?.toLowerCase();
      if (!ALLOWED_TYPES.includes(ext)) {
        setError(`不支持的文件类型: ${ext}`);
        return;
      }
      if (file.size > 20 * 1024 * 1024) {
        setError("文件过大，最大 20MB");
        return;
      }
      setUploading(true);
      setError(null);
      try {
        await uploadDocument(file);
        await fetchDocs();
      } catch (error) {
        setError(formatApiError(error, "上传失败"));
      } finally {
        setUploading(false);
      }
    },
    [fetchDocs]
  );

  const handleDelete = useCallback(
    async (id: string) => {
      try {
        await deleteDocument(id);
        await fetchDocs();
      } catch (error) {
        setError(formatApiError(error, "删除失败"));
      }
    },
    [fetchDocs]
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) handleUpload(file);
    },
    [handleUpload]
  );

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) handleUpload(file);
      if (fileInputRef.current) fileInputRef.current.value = "";
    },
    [handleUpload]
  );

  return (
    <div className="page-content">
      <h2>文档库</h2>

      {/* Upload zone */}
      <div
        className={`upload-zone ${dragging ? "dragging" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
      >
        <p style={{ margin: 0, fontSize: 15 }}>
          {uploading ? "上传中..." : "点击或拖拽文件到此处上传"}
        </p>
        <div className="upload-types">
          {ALLOWED_TYPES.map((t) => (
            <span key={t} className="type-tag">{t}</span>
          ))}
        </div>
        <input
          ref={fileInputRef}
          type="file"
          accept={ALLOWED_TYPES.join(",")}
          style={{ display: "none" }}
          onChange={handleFileChange}
        />
      </div>

      {error && (
        <div style={{ color: "var(--danger)", marginBottom: 12, fontSize: 13 }}>{error}</div>
      )}

      {/* Document table */}
      {loading ? (
        <p style={{ color: "var(--muted)" }}>加载中...</p>
      ) : docs.length === 0 ? (
        <p style={{ color: "var(--muted)" }}>暂无文档，上传一个试试</p>
      ) : (
        <table className="doc-table">
          <thead>
            <tr>
              <th>文件名</th>
              <th>类型</th>
              <th>大小</th>
              <th>分块</th>
              <th>状态</th>
              <th>时间</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.id}>
                <td className="doc-name">{d.filename}</td>
                <td className="doc-meta">{d.file_type.toUpperCase()}</td>
                <td className="doc-meta">{formatSize(d.file_size)}</td>
                <td className="doc-meta">{d.chunk_count}</td>
                <td>
                  <span className={`status-badge ${STATUS_CLASS[d.status] || ""}`}>
                    {STATUS_LABELS[d.status] || d.status}
                  </span>
                </td>
                <td className="doc-meta">
                  {d.created_at ? new Date(d.created_at).toLocaleDateString("zh-CN") : "-"}
                </td>
                <td>
                  <div className="doc-actions">
                    <button
                      className="doc-btn danger"
                      onClick={() => handleDelete(d.id)}
                    >
                      删除
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
