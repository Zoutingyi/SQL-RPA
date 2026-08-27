import { useEffect, useState, useCallback } from "react";
import {
  listMemories, updateMemory, deleteMemory, clearAllMemories,
  getProfile, generateProfile,
  type MemoryItem, type ProfileResponse,
} from "../../api/memories";
import { useToastStore } from "../../stores/toastStore";
import { useConfirm } from "../shared/confirmContext";

export function MemoryList() {
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [profile, setProfile] = useState<ProfileResponse["profile"] | undefined>();
  const [generating, setGenerating] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState("");
  const [filterType, setFilterType] = useState("");

  const addToast = useToastStore((s) => s.addToast);
  const confirm = useConfirm();

  const load = useCallback(async () => {
    try {
      const [memData, profData] = await Promise.all([
        listMemories(),
        getProfile(),
      ]);
      setMemories(memData.memories);
      setProfile(profData.profile);
    } catch {
      addToast({ type: "error", message: "无法加载记忆数据" });
    } finally {
      setLoading(false);
    }
  }, [addToast]);

  useEffect(() => {
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleDelete = async (id: string, content: string) => {
    const ok = await confirm({
      title: "删除记忆",
      message: `确定要删除这条记忆吗？\n\n${content.slice(0, 100)}${content.length > 100 ? "..." : ""}`,
      confirmLabel: "删除",
      variant: "danger",
    });
    if (!ok) return;

    try {
      await deleteMemory(id);
      setMemories((prev) => prev.filter((m) => m.id !== id));
      addToast({ type: "success", message: "记忆已删除" });
    } catch {
      addToast({ type: "error", message: "删除失败" });
    }
  };

  const handleClearAll = async () => {
    if (memories.length === 0) return;
    const ok = await confirm({
      title: "清除所有记忆",
      message: `确定要删除全部 ${memories.length} 条记忆吗？此操作不可撤销。`,
      confirmLabel: "全部清除",
      variant: "danger",
    });
    if (!ok) return;

    try {
      const result = await clearAllMemories();
      setMemories([]);
      addToast({ type: "success", message: `已清除 ${result.deleted_count} 条记忆` });
    } catch {
      addToast({ type: "error", message: "清除失败" });
    }
  };

  const handleDeprecate = async (id: string, deprecated: boolean) => {
    try {
      await updateMemory(id, { deprecated });
      setMemories((prev) => prev.map((m) => (m.id === id ? { ...m, deprecated } : m)));
      addToast({ type: "success", message: deprecated ? "已标记为废弃" : "已恢复" });
    } catch {
      addToast({ type: "error", message: "更新失败" });
    }
  };

  const handleEdit = (m: MemoryItem) => {
    setEditingId(m.id);
    setEditContent(m.content);
  };

  const handleSaveEdit = async () => {
    if (!editingId || !editContent.trim()) return;
    try {
      await updateMemory(editingId, { content: editContent.trim() });
      setMemories((prev) => prev.map((m) => (m.id === editingId ? { ...m, content: editContent.trim() } : m)));
      setEditingId(null);
      addToast({ type: "success", message: "记忆已更新" });
    } catch {
      addToast({ type: "error", message: "更新失败" });
    }
  };

  const handleGenerateProfile = async () => {
    setGenerating(true);
    try {
      const result = await generateProfile();
      setProfile(result.profile);
      if (result.profile) {
        addToast({ type: "success", message: "用户画像已生成" });
      } else {
        addToast({ type: "info", message: result.message || "暂无足够记忆以生成画像" });
      }
    } catch {
      addToast({ type: "error", message: "画像生成失败" });
    } finally {
      setGenerating(false);
    }
  };

  if (loading) {
    return (
      <div className="page-content">
        <h2>记忆管理</h2>
        <p style={{ color: "var(--muted)" }}>加载中...</p>
      </div>
    );
  }

  const types = [...new Set(memories.map((m) => m.memory_type))];
  const filtered = filterType
    ? memories.filter((m) => m.memory_type === filterType)
    : memories;

  const activeCount = memories.filter((m) => !m.deprecated).length;
  const deprecatedCount = memories.filter((m) => m.deprecated).length;

  return (
    <div className="page-content" style={{ maxWidth: 800 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>记忆管理</h2>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn-secondary" onClick={handleGenerateProfile} disabled={generating} style={{ padding: "6px 14px", fontSize: 13 }}>
            {generating ? "生成中..." : "生成用户画像"}
          </button>
          <button className="btn-secondary" onClick={handleClearAll} disabled={memories.length === 0} style={{ padding: "6px 14px", fontSize: 13 }}>
            清除全部
          </button>
        </div>
      </div>

      {/* Stats */}
      <div style={{ display: "flex", gap: 16, marginBottom: 16, fontSize: 13, color: "var(--muted)" }}>
        <span>总计: {memories.length}</span>
        <span style={{ color: "var(--success)" }}>活跃: {activeCount}</span>
        <span style={{ color: "var(--warn)" }}>废弃: {deprecatedCount}</span>
        {types.map((t) => (
          <button
            key={t}
            onClick={() => setFilterType(filterType === t ? "" : t)}
            style={{
              padding: "1px 8px", fontSize: 11, cursor: "pointer",
              background: filterType === t ? "var(--accent-soft)" : "var(--overlay-subtle)",
              border: `1px solid ${filterType === t ? "var(--accent)" : "var(--border)"}`,
              borderRadius: "var(--radius-sm)", color: filterType === t ? "var(--accent)" : "var(--muted)",
            }}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Profile card */}
      {profile && (
        <div style={{
          padding: "12px 16px", marginBottom: 16,
          background: "var(--accent-dim)", borderLeft: "3px solid var(--accent)",
          borderRadius: "0 var(--radius-sm) var(--radius-sm) 0",
          fontSize: 13,
        }}>
          <div style={{ fontWeight: 600, marginBottom: 4, color: "var(--accent)" }}>
            用户画像 v{profile.version}
          </div>
          <pre style={{
            margin: 0, font: "12px/1.5 var(--font-sans)", color: "var(--fg)",
            whiteSpace: "pre-wrap", wordBreak: "break-word",
          }}>
            {JSON.stringify(profile.profile_data, null, 2)}
          </pre>
        </div>
      )}

      {/* Memory list */}
      {filtered.length === 0 ? (
        <div style={{ padding: 40, textAlign: "center", color: "var(--muted)", fontSize: 14 }}>
          {memories.length === 0 ? "暂无记忆数据" : "没有匹配的记忆"}
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {filtered.map((m) => (
            <div
              key={m.id}
              style={{
                padding: "10px 14px",
                background: m.deprecated ? "var(--overlay-subtle)" : "var(--surface)",
                border: `1px solid ${m.deprecated ? "var(--border)" : "var(--border-light)"}`,
                borderRadius: "var(--radius)",
                opacity: m.deprecated ? 0.6 : 1,
              }}
            >
              {editingId === m.id ? (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <textarea
                    value={editContent}
                    onChange={(e) => setEditContent(e.target.value)}
                    style={{
                      width: "100%", minHeight: 60, padding: "8px 10px",
                      font: "13px/1.5 var(--font-sans)", color: "var(--fg)",
                      background: "var(--bg)", border: "1px solid var(--border)",
                      borderRadius: "var(--radius-sm)", resize: "vertical", outline: "none",
                    }}
                  />
                  <div style={{ display: "flex", gap: 6 }}>
                    <button onClick={handleSaveEdit} style={{ padding: "3px 12px", fontSize: 12, cursor: "pointer", background: "var(--accent)", color: "var(--bg)", border: "none", borderRadius: "var(--radius-sm)" }}>
                      保存
                    </button>
                    <button onClick={() => setEditingId(null)} style={{ padding: "3px 12px", fontSize: 12, cursor: "pointer", background: "var(--overlay-subtle)", color: "var(--fg)", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)" }}>
                      取消
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <div style={{ fontSize: 14, lineHeight: 1.5, marginBottom: 6, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                    {m.content}
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 11, color: "var(--muted)" }}>
                    <span style={{
                      padding: "0 6px", borderRadius: 3, fontSize: 10, fontWeight: 550,
                      background: "var(--overlay-subtle)", border: "1px solid var(--border)",
                    }}>
                      {m.memory_type}
                    </span>
                    <span>{new Date(m.created_at).toLocaleDateString()}</span>
                    <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
                      <button onClick={() => handleEdit(m)} style={actionBtnStyle}>编辑</button>
                      <button onClick={() => handleDeprecate(m.id, !m.deprecated)} style={actionBtnStyle}>
                        {m.deprecated ? "恢复" : "废弃"}
                      </button>
                      <button onClick={() => handleDelete(m.id, m.content)} style={{ ...actionBtnStyle, color: "var(--danger)" }}>
                        删除
                      </button>
                    </div>
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const actionBtnStyle: React.CSSProperties = {
  padding: "2px 8px", fontSize: 11, cursor: "pointer",
  background: "transparent", color: "var(--muted)",
  border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
};
