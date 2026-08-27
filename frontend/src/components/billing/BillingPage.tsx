import { useEffect, useState } from "react";
import { listInvoices, type Invoice } from "../../api/billing";
import { formatApiError } from "../../api/client";

export function BillingPage() {
  const [items, setItems] = useState<Invoice[]>([]);
  const [error, setError] = useState("");
  useEffect(() => { listInvoices().then((result) => setItems(result.items)).catch((caught) => setError(formatApiError(caught, "加载账单失败"))); }, []);
  return <div className="page-content" style={{ maxWidth: 960 }}><div className="usage-header"><div><h2>部门账单</h2><p style={{ color: "var(--muted)" }}>当前部门的账单金额和支付状态均来自服务端。</p></div></div>{error && <div className="rollback-error" role="alert">{error}</div>}<div className="doc-table-wrap"><table className="doc-table"><thead><tr><th>账单周期</th><th>用户</th><th>金额</th><th>状态</th><th>到期时间</th></tr></thead><tbody>{items.length ? items.map((invoice) => <tr key={invoice.id}><td className="doc-meta">{new Date(invoice.period_start).toLocaleDateString("zh-CN")} – {new Date(invoice.period_end).toLocaleDateString("zh-CN")}</td><td className="doc-meta">{invoice.user_id || "全部用户"}</td><td className="doc-meta">{invoice.currency} {invoice.total_usd.toFixed(4)}</td><td><span className={`status-badge ${invoice.status === "paid" ? "ready" : "processing"}`}>{invoice.status === "paid" ? "已支付" : invoice.status}</span></td><td className="doc-meta">{new Date(invoice.due_at).toLocaleString("zh-CN")}</td></tr>) : <tr><td colSpan={5} className="log-empty">暂无账单</td></tr>}</tbody></table></div></div>;
}
