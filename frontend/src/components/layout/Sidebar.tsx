import { useEffect, useState, useRef } from "react";
import { useChatStore } from "../../stores/chatStore";
import { useToastStore } from "../../stores/toastStore";
import { useConfirm } from "../shared/confirmContext";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ChatIcon, DocIcon, SettingsIcon, BrainIcon, DatabaseIcon, PlusIcon, TrashIcon, UserIcon, UsageIcon } from "../shared/Icons";
import { useAuthStore } from "../../stores/authStore";
import { getUnreadCount } from "../../api/notifications";
import { useOrganizationStore } from "../../stores/organizationStore";
import { hasPlatformAdminAccess } from "../auth/userAccess";

export function Sidebar() {
  const {
    conversations, loadConversations, newConversation,
    currentConvId, switchConversation, deleteConversation, renameConversation,
  } = useChatStore();
  const addToast = useToastStore((s) => s.addToast);
  const confirm = useConfirm();
  const location = useLocation();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const {
    memberships, currentContext, contextRevision, switching,
    switchMembership,
  } = useOrganizationStore();
  const effectiveRole = currentContext ? currentContext.role : user?.role;
  const platformAdmin = hasPlatformAdminAccess(user);
  const departmentAdmin = platformAdmin
    || currentContext?.role === "admin"
    || currentContext?.permissions.includes("organization.manage") === true;
  const departmentMode = Boolean(currentContext) || user?.is_platform_admin !== true;
  const currentMembership = memberships.find((membership) => membership.id === currentContext?.membership_id);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [unreadCount, setUnreadCount] = useState(0);
  const editRef = useRef<HTMLInputElement>(null);

  useEffect(() => { if (departmentMode) void loadConversations(); }, [departmentMode, loadConversations, contextRevision]);
  useEffect(() => {
    if (!departmentMode) {
      setUnreadCount(0);
      return;
    }
    const loadUnread = () => getUnreadCount().then((result) => setUnreadCount(result.count)).catch(() => undefined);
    void loadUnread();
    const timer = window.setInterval(loadUnread, 15000);
    return () => window.clearInterval(timer);
  }, [departmentMode, contextRevision]);

  const handleMembershipChange = async (membershipId: string) => {
    const target = memberships.find((item) => item.id === membershipId);
    if (!target) return;
    const ok = await confirm({
      title: target.compatibility_mode ? "切换部门" : `切换到${target.is_primary ? "主职" : "兼职"}部门`,
      message: `${target.organization_path.join(" / ")}\n角色：${target.compatibility_mode ? "切换后由后端确认" : target.role}\n切换后将清空当前部门页面数据并终止进行中的请求。`,
      confirmLabel: "确认切换",
    });
    if (!ok) return;
    if (await switchMembership(membershipId)) {
      await useAuthStore.getState().loadMe();
      addToast({ type: "success", message: "部门身份已切换" });
    }
  };

  useEffect(() => {
    if (editingId) editRef.current?.focus();
  }, [editingId]);

  const handleNew = async () => {
    await newConversation();
    addToast({ type: "success", message: "新对话已创建" });
  };

  const handleDelete = async (e: React.MouseEvent, id: string, title: string) => {
    e.stopPropagation();
    const ok = await confirm({
      title: "删除会话",
      message: `确定要删除「${title}」吗？此操作不可撤销。`,
      variant: "danger",
      confirmLabel: "删除",
    });
    if (ok) {
      await deleteConversation(id);
      addToast({ type: "success", message: "会话已删除" });
    }
  };

  const startRename = (e: React.MouseEvent, id: string, title: string) => {
    e.stopPropagation();
    setEditingId(id);
    setEditTitle(title);
  };

  const finishRename = async () => {
    if (editingId && editTitle.trim()) {
      await renameConversation(editingId, editTitle.trim());
    }
    setEditingId(null);
    setEditTitle("");
  };

  const handleRenameKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") finishRename();
    if (e.key === "Escape") { setEditingId(null); setEditTitle(""); }
  };

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div className="sidebar-logo">
          SQL<span>RPA</span>
        </div>
        {departmentMode && <button className="sidebar-new-btn" onClick={handleNew}>
          <PlusIcon size={12} /> 新对话
        </button>}
      </div>

      <nav className="sidebar-nav">
        {departmentMode && <Link to="/" className={location.pathname === "/" ? "active" : ""}>
          <ChatIcon /> 对话
        </Link>}
        {departmentMode && <Link to="/documents" className={location.pathname === "/documents" ? "active" : ""}>
          <DocIcon /> 文档库
        </Link>}
        {departmentMode && <Link to="/database" className={location.pathname === "/database" ? "active" : ""}>
          <DatabaseIcon /> 数据库
        </Link>}
        {platformAdmin && (
          <Link to="/settings" className={location.pathname === "/settings" ? "active" : ""}>
            <SettingsIcon /> 设置
          </Link>
        )}
        {departmentMode && (effectiveRole === "approver" || effectiveRole === "admin") && (
          <Link to="/usage" className={location.pathname === "/usage" ? "active" : ""}>
            <UsageIcon /> 用量
          </Link>
        )}
        {departmentMode && <Link to="/memories" className={location.pathname === "/memories" ? "active" : ""}>
          <BrainIcon /> 记忆
        </Link>}
        {departmentMode && <Link to="/notifications" className={location.pathname === "/notifications" ? "active" : ""}>
          <ChatIcon /> 通知 {unreadCount > 0 && <span className="nav-count">{unreadCount > 99 ? "99+" : unreadCount}</span>}
        </Link>}
        {departmentMode && (effectiveRole === "approver" || effectiveRole === "admin") && (
          <Link to="/billing" className={location.pathname === "/billing" ? "active" : ""}>
            <UsageIcon /> 账单
          </Link>
        )}
        {platformAdmin && (
          <Link to="/users" className={location.pathname === "/users" ? "active" : ""}>
            <UserIcon /> 用户管理
          </Link>
        )}
        {departmentAdmin && (
          <Link to="/departments" className={location.pathname === "/departments" ? "active" : ""}>
            <UserIcon /> 部门管理
          </Link>
        )}
      </nav>

      {departmentMode && <div className="sidebar-conv-label">历史对话</div>}

      {departmentMode && <div className="sidebar-convs">
        {conversations.length === 0 && (
          <p style={{ padding: "8px 12px", fontSize: 12, color: "var(--muted)" }}>暂无对话</p>
        )}
        {conversations.map((conv) => (
          <div
            key={conv.id}
            className={`sidebar-conv ${conv.id === currentConvId ? "active" : ""}`}
            onClick={() => { switchConversation(conv.id); navigate("/"); }}
          >
            {editingId === conv.id ? (
              <input
                ref={editRef}
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                onBlur={finishRename}
                onKeyDown={handleRenameKey}
                onClick={(e) => e.stopPropagation()}
                style={{
                  flex: 1,
                  background: "var(--accent-dim)",
                  border: "1px solid var(--accent)",
                  borderRadius: "var(--radius-sm)",
                  color: "var(--fg)",
                  fontSize: 13,
                  padding: "2px 6px",
                  outline: "none",
                }}
              />
            ) : (
              <span
                className="sidebar-conv-title"
                title={conv.title}
                onDoubleClick={(e) => startRename(e, conv.id, conv.title)}
              >
                {conv.title}
              </span>
            )}
            <button
              className="sidebar-conv-del"
              onClick={(e) => handleDelete(e, conv.id, conv.title)}
              title="删除"
            >
              <TrashIcon size={12} />
            </button>
          </div>
        ))}
      </div>}

      <div className="sidebar-user">
        {memberships.length > 0 && <label className="department-switcher">
          <span>当前部门</span>
          <select value={currentContext?.membership_id || ""} onChange={(event) => void handleMembershipChange(event.target.value)} disabled={memberships.length <= 1 || switching}>
            {memberships.map((membership) => <option key={membership.id} value={membership.id}>{membership.organization_path.join(" / ")} · {membership.compatibility_mode ? "兼容身份" : membership.is_primary ? "主职" : "兼职"}</option>)}
          </select>
        </label>}
        {currentContext ? <div className="sidebar-organization-path" title={currentContext.organization_path.join(" / ")}>{currentContext.organization_path.join(" / ")}{currentMembership?.job_title ? ` · ${currentMembership.job_title}` : ""} · {currentMembership?.is_primary ? "主职" : "兼职"}</div>
          : platformAdmin && <div className="sidebar-organization-path">平台管理模式</div>}
        <div className="sidebar-user-name">
          {user?.display_name || user?.username || "未登录"}
        </div>
        <div className="sidebar-user-role">{effectiveRole || (platformAdmin ? "平台管理员" : "未分配")}</div>
        <Link className="sidebar-user-profile" to="/profile">个人设置</Link>
        <button className="sidebar-user-logout" onClick={logout}>退出</button>
      </div>
    </aside>
  );
}
