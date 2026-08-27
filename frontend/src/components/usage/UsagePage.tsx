import { useEffect, useState } from "react";
import { getUsageOverview, getUsageSummary, type UsageOverview } from "../../api/usage";
import { useToastStore } from "../../stores/toastStore";
import { formatApiError } from "../../api/client";
import { useAuthStore } from "../../stores/authStore";
import { useOrganizationStore } from "../../stores/organizationStore";

export function UsagePage() {
  const addToast = useToastStore((s) => s.addToast);
  const [summary, setSummary] = useState<UsageOverview | null>(null);
  const user = useAuthStore((state) => state.user);
  const currentContext = useOrganizationStore((state) => state.currentContext);
  const effectiveRole = currentContext ? currentContext.role : user?.role;
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(false);

  const load = async (nextDays = days) => {
    setLoading(true);
    try {
      const data = effectiveRole === "admin"
        ? await getUsageOverview(nextDays)
        : await getUsageSummary(nextDays).then((result) => ({
            days: result.days,
            total_cost_usd: result.items.reduce((sum, item) => sum + (item.cost_usd ?? 0), 0),
            total_tokens: result.items.reduce((sum, item) => sum + item.total_tokens, 0),
            by_model: result.items, by_user: [], quotas: [], tenant_scope: "current_user_summary",
          } satisfies UsageOverview));
      setSummary(data);
    } catch (error) {
      addToast({ type: "error", message: formatApiError(error, "加载用量统计失败") });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const totalTokens =
    summary?.total_tokens ?? 0;
  const hasCostData = !!summary;
  const totalCost = summary?.total_cost_usd ?? 0;
  const currentQuota = summary?.quotas.find((quota) => quota.user_id === user?.id && quota.enabled);
  const currentUsage = summary?.by_user.find((item) => item.user_id === user?.id);
  const quotaPercent = currentQuota?.monthly_token_limit
    ? Math.min(100, Math.round(((currentUsage?.total_tokens ?? 0) / currentQuota.monthly_token_limit) * 100))
    : null;

  return (
    <div className="page-content" style={{ maxWidth: 960 }}>
      <div className="usage-header">
        <div>
          <h2>部门用量</h2>
          <p style={{ color: "var(--muted)" }}>
            当前部门：{currentContext?.organization_path.join(" / ") || "未知"}。按模型聚合最近 LLM 调用次数、Token 与成本。
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <select
            className="log-filter-select"
            value={days}
            onChange={(e) => {
              const next = Number(e.target.value);
              setDays(next);
              load(next);
            }}
          >
            <option value={7}>近 7 天</option>
            <option value={30}>近 30 天</option>
            <option value={90}>近 90 天</option>
          </select>
          <button className="page-btn" onClick={() => load(days)} disabled={loading}>
            {loading ? "加载中..." : "刷新"}
          </button>
        </div>
      </div>
      {effectiveRole === "admin" && summary?.by_user.length ? <div className="review-section"><div className="review-section-title">按用户聚合</div><div className="doc-table-wrap"><table className="doc-table"><thead><tr><th>用户</th><th>请求数</th><th>Token</th><th>成本 (USD)</th></tr></thead><tbody>{summary.by_user.map((item) => <tr key={item.user_id}><td className="doc-meta">{item.user_id}</td><td className="doc-meta">{item.requests.toLocaleString()}</td><td className="doc-meta">{item.total_tokens.toLocaleString()}</td><td className="doc-meta">${item.cost_usd.toFixed(4)}</td></tr>)}</tbody></table></div></div> : null}

      <div className="usage-total-card">
        <span className="usage-total-label">总 Token</span>
        <span className="usage-total-value">{totalTokens.toLocaleString()}</span>
        <span className="usage-total-label">请求数</span>
        <span className="usage-total-value">
          {(summary?.by_model.reduce((sum, item) => sum + item.requests, 0) ?? 0).toLocaleString()}
        </span>
        {hasCostData && <><span className="usage-total-label">估算成本</span><span className="usage-total-value">${totalCost.toFixed(4)}</span></>}
      </div>

      {quotaPercent !== null && (
        <div className={`usage-quota ${quotaPercent >= 90 ? "danger" : quotaPercent >= 75 ? "warn" : ""}`}>
          <div><span>Token 配额</span><strong>{(currentUsage?.total_tokens ?? 0).toLocaleString()} / {currentQuota!.monthly_token_limit.toLocaleString()}</strong></div>
          <div className="usage-quota-track"><span style={{ width: `${quotaPercent}%` }} /></div>
          <small>已使用 {quotaPercent}%{quotaPercent >= 90 ? "，即将达到配额上限" : ""}</small>
        </div>
      )}

      <div className="doc-table-wrap" style={{ marginTop: 16 }}>
        <table className="doc-table">
          <thead>
            <tr>
              <th>模型</th>
              <th>请求数</th>
              <th>Prompt Tokens</th>
              <th>Completion Tokens</th>
              <th>总 Tokens</th>
              {hasCostData && <th>成本 (USD)</th>}
            </tr>
          </thead>
          <tbody>
            {summary?.by_model.length ? (
              summary.by_model.map((item) => (
                <tr key={item.model}>
                  <td className="doc-meta">{item.model}</td>
                  <td className="doc-meta">{item.requests.toLocaleString()}</td>
                  <td className="doc-meta">{item.prompt_tokens.toLocaleString()}</td>
                  <td className="doc-meta">{item.completion_tokens.toLocaleString()}</td>
                  <td className="doc-meta">{item.total_tokens.toLocaleString()}</td>
                  {hasCostData && <td className="doc-meta">${(item.cost_usd ?? 0).toFixed(4)}</td>}
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={hasCostData ? 6 : 5} className="log-empty">暂无用量数据</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
