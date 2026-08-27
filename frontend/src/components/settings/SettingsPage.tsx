import { useEffect, useState, useCallback } from "react";
import { getSettings, saveSettings, testConnection, type SettingsResponse } from "../../api/settings";
import { useToastStore } from "../../stores/toastStore";

type CredentialSection = "llm" | "embedding";

const MODEL_DESCRIPTIONS: Record<string, string> = {
  openai: "OpenAI 兼容接口，适合云端模型；生产环境建议同时配置备用模型与调用配额。",
  deepseek: "OpenAI 兼容协议，适合中文与代码任务；请确认 Base URL 和模型名称匹配服务商文档。",
  ollama: "本地模型服务，不产生外部 API 账单，但需要自行保障算力、并发和可用性。",
};

interface CredentialForm {
  provider: string;
  model: string;
  api_key: string;
  base_url: string;
}

export function SettingsPage() {
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<CredentialSection | null>(null);

  const [llm, setLlm] = useState<CredentialForm>({ provider: "", model: "", api_key: "", base_url: "" });
  const [embedding, setEmbedding] = useState<CredentialForm>({ provider: "", model: "", api_key: "", base_url: "" });

  const [toggles, setToggles] = useState({
    web_search_enabled: false,
    rerank_enabled: false,
    dedup_enabled: false,
    memory_enabled: false,
    ocr_enabled: false,
  });
  const [retrievalTopK, setRetrievalTopK] = useState(8);
  const [webSearchMaxResults, setWebSearchMaxResults] = useState(5);

  const addToast = useToastStore((s) => s.addToast);

  const load = useCallback(async () => {
    try {
      const s = await getSettings();
      setSettings(s);
      setLlm(s.llm);
      setEmbedding(s.embedding);
      setToggles({
        web_search_enabled: s.web_search_enabled,
        rerank_enabled: s.rerank_enabled,
        dedup_enabled: s.dedup_enabled,
        memory_enabled: s.memory_enabled,
        ocr_enabled: s.ocr_enabled,
      });
      setRetrievalTopK(s.retrieval_top_k);
      setWebSearchMaxResults(s.web_search_max_results);
    } catch {
      addToast({ type: "error", message: "无法加载设置" });
    } finally {
      setLoading(false);
    }
  }, [addToast]);

  useEffect(() => {
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSave = async () => {
    setSaving(true);
    try {
      const result = await saveSettings({
        llm_provider: llm.provider,
        llm_model: llm.model,
        llm_api_key: llm.api_key || undefined,
        llm_base_url: llm.base_url,
        embedding_provider: embedding.provider,
        embedding_model: embedding.model,
        embedding_api_key: embedding.api_key || undefined,
        embedding_base_url: embedding.base_url,
        ...toggles,
        retrieval_top_k: retrievalTopK,
        web_search_max_results: webSearchMaxResults,
      });
      addToast({ type: "success", message: `设置已保存 (${result.updated.length} 项)` });
    } catch (e: unknown) {
      addToast({ type: "error", message: `保存失败: ${e instanceof Error ? e.message : "未知错误"}` });
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async (kind: CredentialSection) => {
    const cfg = kind === "llm" ? llm : embedding;
    setTesting(kind);
    try {
      const result = await testConnection({ ...cfg, kind });
      if (result.ok) {
        addToast({ type: "success", message: `${kind === "llm" ? "LLM" : "Embedding"} 连接成功 — ${result.latency_ms}ms` });
      } else {
        addToast({ type: "error", message: result.detail || "连接失败" });
      }
    } catch (e: unknown) {
      addToast({ type: "error", message: `测试失败: ${e instanceof Error ? e.message : "未知错误"}` });
    } finally {
      setTesting(null);
    }
  };

  if (loading) {
    return (
      <div className="page-content">
        <h2>设置</h2>
        <p style={{ color: "var(--muted)" }}>加载中...</p>
      </div>
    );
  }

  return (
    <div className="page-content" style={{ maxWidth: 680 }}>
      <h2>设置</h2>

      {/* ── LLM ── */}
      <Section title="LLM 大模型">
        <Field label="Provider" value={llm.provider} onChange={(v) => setLlm({ ...llm, provider: v })} placeholder="openai" />
        <Field label="Model" value={llm.model} onChange={(v) => setLlm({ ...llm, model: v })} placeholder="gpt-4o" />
        <Field label="API Key" type="password" value={llm.api_key} onChange={(v) => setLlm({ ...llm, api_key: v })} placeholder={settings?.llm.api_key === "***" ? "(已设置)" : ""} />
        <Field label="Base URL" value={llm.base_url} onChange={(v) => setLlm({ ...llm, base_url: v })} placeholder="https://api.openai.com/v1" />
        <div className="model-preset-note">{MODEL_DESCRIPTIONS[llm.provider.toLowerCase()] || "自定义 Provider：保存前请先测试连接，并确认流式输出与用量统计兼容。"}</div>
        <div style={{ marginTop: 8 }}>
          <button className="btn-secondary" onClick={() => handleTest("llm")} disabled={testing === "llm"}>
            {testing === "llm" ? "测试中..." : "测试 LLM 连接"}
          </button>
        </div>
      </Section>

      {/* ── Embedding ── */}
      <Section title="Embedding 嵌入模型">
        <Field label="Provider" value={embedding.provider} onChange={(v) => setEmbedding({ ...embedding, provider: v })} placeholder="openai" />
        <Field label="Model" value={embedding.model} onChange={(v) => setEmbedding({ ...embedding, model: v })} placeholder="text-embedding-3-small" />
        <Field label="API Key" type="password" value={embedding.api_key} onChange={(v) => setEmbedding({ ...embedding, api_key: v })} placeholder={settings?.embedding.api_key === "***" ? "(已设置)" : ""} />
        <Field label="Base URL" value={embedding.base_url} onChange={(v) => setEmbedding({ ...embedding, base_url: v })} placeholder="https://api.openai.com/v1" />
        <div className="model-preset-note">Embedding 模型决定知识库检索向量维度。更换已有知识库的模型后通常需要重新建立索引。</div>
        <div style={{ marginTop: 8 }}>
          <button className="btn-secondary" onClick={() => handleTest("embedding")} disabled={testing === "embedding"}>
            {testing === "embedding" ? "测试中..." : "测试 Embedding 连接"}
          </button>
        </div>
      </Section>

      {/* ── Retrieval ── */}
      <Section title="检索设置">
        <div className="setting-row">
          <span className="setting-label">retrieval_top_k</span>
          <input
            type="number" min={1} max={50}
            value={retrievalTopK}
            onChange={(e) => setRetrievalTopK(Number(e.target.value))}
            style={{ width: 80 }}
          />
        </div>
        <div className="setting-row">
          <span className="setting-label">web_search_max_results</span>
          <input
            type="number" min={1} max={10}
            value={webSearchMaxResults}
            onChange={(e) => setWebSearchMaxResults(Number(e.target.value))}
            style={{ width: 80 }}
          />
        </div>
      </Section>

      {/* ── Toggles ── */}
      <Section title="功能开关">
        <Toggle label="Web Search 网络搜索" checked={toggles.web_search_enabled} onChange={(v) => setToggles({ ...toggles, web_search_enabled: v })} />
        <Toggle label="Reranker 重排序" checked={toggles.rerank_enabled} onChange={(v) => setToggles({ ...toggles, rerank_enabled: v })} />
        <Toggle label="Dedup 去重" checked={toggles.dedup_enabled} onChange={(v) => setToggles({ ...toggles, dedup_enabled: v })} />
        <Toggle label="Memory 记忆" checked={toggles.memory_enabled} onChange={(v) => setToggles({ ...toggles, memory_enabled: v })} />
        <Toggle label="OCR 文字识别" checked={toggles.ocr_enabled} onChange={(v) => setToggles({ ...toggles, ocr_enabled: v })} />
      </Section>

      {/* ── Save ── */}
      <div style={{ marginTop: 24, display: "flex", gap: 12, alignItems: "center" }}>
        <button className="btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? "保存中..." : "保存设置"}
        </button>
        <button className="btn-secondary" onClick={load} disabled={loading}>
          重置
        </button>
      </div>
    </div>
  );
}

/* ── Sub-components ── */

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: 20 }}>
      <h3 style={{ fontSize: 14, fontWeight: 600, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 10 }}>
        {title}
      </h3>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {children}
      </div>
    </div>
  );
}

function Field({ label, type = "text", value, onChange, placeholder }: {
  label: string; type?: string; value: string;
  onChange: (v: string) => void; placeholder?: string;
}) {
  return (
    <div className="setting-row">
      <span className="setting-label">{label}</span>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        style={{ width: type === "number" ? 100 : 320 }}
      />
    </div>
  );
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="setting-row">
      <span className="setting-label">{label}</span>
      <label className="toggle-switch">
        <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        <span className="toggle-slider" />
      </label>
    </div>
  );
}
