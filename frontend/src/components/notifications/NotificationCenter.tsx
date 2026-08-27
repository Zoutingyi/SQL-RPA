import { useCallback, useEffect, useState } from "react";
import { createNotificationEndpoint, getNotificationPreferences, listNotificationEndpoints, listNotifications, markNotificationRead, setNotificationPreference, testNotificationEndpoint, type NotificationEndpoint, type NotificationItem, type NotificationPreference } from "../../api/notifications";
import { formatApiError } from "../../api/client";
import { useAuthStore } from "../../stores/authStore";
import { useOrganizationStore } from "../../stores/organizationStore";

export function NotificationCenter() {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [preferences, setPreferences] = useState<NotificationPreference[]>([]);
  const [endpoints, setEndpoints] = useState<NotificationEndpoint[]>([]);
  const [kind, setKind] = useState("webhook");
  const [target, setTarget] = useState("");
  const user = useAuthStore((state) => state.user);
  const currentContext = useOrganizationStore((state) => state.currentContext);
  const effectiveRole = currentContext ? currentContext.role : user?.role;
  const load = useCallback(async () => {
    setLoading(true);
    try { setItems((await listNotifications(unreadOnly)).items); setError(""); }
    catch (caught) { setError(formatApiError(caught, "加载通知失败")); }
    finally { setLoading(false); }
  }, [unreadOnly]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { void getNotificationPreferences().then((result) => setPreferences(result.items)); if (effectiveRole === "admin") void listNotificationEndpoints().then((result) => setEndpoints(result.items)); }, [effectiveRole]);

  const markRead = async (item: NotificationItem) => {
    if (item.read_at) return;
    try { await markNotificationRead(item.id); await load(); }
    catch (caught) { setError(formatApiError(caught, "标记通知失败")); }
  };

  const togglePreference = async (channel: NotificationPreference["channel"]) => {
    const current = preferences.find((item) => item.event_type === "*" && item.channel === channel);
    const saved = await setNotificationPreference({ event_type: "*", channel, enabled: !(current?.enabled ?? true) });
    setPreferences((items) => [...items.filter((item) => !(item.event_type === "*" && item.channel === channel)), saved]);
  };

  return <div className="page-content" style={{ maxWidth: 900 }}>
    <div className="usage-header"><div><h2>通知中心</h2><p style={{ color: "var(--muted)" }}>审批状态、执行恢复和系统事件。</p></div><label className="notification-filter"><input type="checkbox" checked={unreadOnly} onChange={(event) => setUnreadOnly(event.target.checked)} /> 仅看未读</label></div>
    {error && <div className="rollback-error" role="alert">{error}</div>}
    {loading ? <div className="log-loading">加载中...</div> : items.length === 0 ? <div className="log-empty">暂无通知</div> : <div className="notification-list">{items.map((item) => <button key={item.id} className={`notification-item ${item.read_at ? "read" : "unread"}`} onClick={() => markRead(item)}><span className="notification-dot" /><div><strong>{item.title}</strong><p>{item.body}</p><small>{new Date(item.created_at).toLocaleString("zh-CN")} · {item.event_type}</small></div></button>)}</div>}
    <div className="review-section"><div className="review-section-title">通知偏好</div><div className="review-state-actions">{(["in_app", "webhook", "email", "im"] as const).map((channel) => { const enabled = preferences.find((item) => item.event_type === "*" && item.channel === channel)?.enabled ?? true; return <button className="page-btn" key={channel} onClick={() => void togglePreference(channel)}>{channel}: {enabled ? "开启" : "关闭"}</button>; })}</div></div>
    {effectiveRole === "admin" && <div className="review-section"><div className="review-section-title">通知端点</div><div className="review-state-actions"><select className="log-filter-select" value={kind} onChange={(event) => setKind(event.target.value)}><option value="webhook">Webhook</option><option value="email">邮件适配器</option><option value="im">IM 适配器</option></select><input className="review-input" value={target} onChange={(event) => setTarget(event.target.value)} placeholder="HTTPS 接收端点" /><button className="page-btn" disabled={!target.trim()} onClick={async () => { try { await createNotificationEndpoint({ kind, target, enabled: true }); setTarget(""); setEndpoints((await listNotificationEndpoints()).items); } catch (caught) { setError(formatApiError(caught, "创建端点失败")); } }}>添加</button></div><div className="notification-list">{endpoints.map((endpoint) => <div className="notification-item" key={endpoint.id}><div><strong>{endpoint.kind}</strong><p>{endpoint.target}</p></div><button className="page-btn" onClick={async () => { try { await testNotificationEndpoint(endpoint.id); } catch (caught) { setError(formatApiError(caught, "测试发送失败")); } }}>测试发送</button></div>)}</div></div>}
  </div>;
}
