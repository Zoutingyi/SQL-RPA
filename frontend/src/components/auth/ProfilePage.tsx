import { useRef, useState } from "react";
import { changePassword } from "../../api/auth";
import { formatApiError } from "../../api/client";
import { useAuthStore } from "../../stores/authStore";
import { useOrganizationStore } from "../../stores/organizationStore";
import { useToastStore } from "../../stores/toastStore";
import { maskPhone } from "./userAccess";

export function ProfilePage({ forced = false, restricted = false }: { forced?: boolean; restricted?: boolean }) {
  const user = useAuthStore((state) => state.user);
  const logout = useAuthStore((state) => state.logout);
  const memberships = useOrganizationStore((state) => state.memberships);
  const currentContext = useOrganizationStore((state) => state.currentContext);
  const addToast = useToastStore((state) => state.addToast);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPasswords, setShowPasswords] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const currentPasswordRef = useRef<HTMLInputElement>(null);

  const visibleMemberships = user?.organization_memberships?.length ? user.organization_memberships : memberships;
  const currentMembership = user?.current_membership
    || visibleMemberships.find((membership) => membership.id === currentContext?.membership_id)
    || null;

  const clearPasswords = () => {
    setCurrentPassword("");
    setNewPassword("");
    setConfirmPassword("");
  };

  const submitPassword = async (event: React.FormEvent) => {
    event.preventDefault();
    if (submitting) return;
    setFormError(null);
    if (!currentPassword || newPassword.length < 8 || newPassword !== confirmPassword || newPassword === "111111") {
      setFormError(newPassword === "111111"
        ? "新密码不能继续使用默认弱密码"
        : newPassword !== confirmPassword
          ? "两次输入的新密码不一致"
          : "请填写当前密码，新密码至少 8 位");
      return;
    }
    setSubmitting(true);
    try {
      await changePassword({
        current_password: currentPassword,
        new_password: newPassword,
        confirm_password: confirmPassword,
      });
      clearPasswords();
      logout();
      addToast({ type: "success", message: "密码已修改，请重新登录" });
    } catch (error) {
      clearPasswords();
      setFormError(formatApiError(error, "密码修改失败"));
      queueMicrotask(() => currentPasswordRef.current?.focus());
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={`profile-page${forced ? " forced-password-page" : ""}`}>
      <header className="profile-header">
        <div>
          <h2>{forced ? "首次登录，请修改密码" : "个人设置"}</h2>
          <p>{forced ? "完成改密并重新登录后才能进入其他功能。" : restricted ? "当前任职尚未分配权限，只能查看个人设置和修改密码。" : "账号和任职资料只读，如需调整请联系管理员。"}</p>
        </div>
        {(forced || restricted) && <button className="sidebar-user-logout" onClick={logout}>退出登录</button>}
      </header>

      {!forced && (
        <section className="profile-card" aria-labelledby="profile-summary-title">
          <h3 id="profile-summary-title">账号资料</h3>
          <dl className="profile-grid">
            <div><dt>登录账号</dt><dd>{user?.username}</dd></div>
            <div><dt>姓名</dt><dd>{user?.display_name || "资料待完善"}</dd></div>
            <div><dt>联系电话</dt><dd>{maskPhone(user?.phone)}</dd></div>
            <div><dt>账号状态</dt><dd>{user?.is_active === false ? "已停用" : "正常"}</dd></div>
            <div><dt>当前部门</dt><dd>{currentContext?.organization_path.join(" / ") || "未选择部门"}</dd></div>
            <div><dt>职位</dt><dd>{currentMembership?.job_title || "资料待完善"}</dd></div>
            <div><dt>任职类型</dt><dd>{currentMembership ? currentMembership.is_primary ? "主职" : "兼职" : "未任职"}</dd></div>
            <div><dt>权限</dt><dd>{currentMembership?.role || "未分配"}</dd></div>
          </dl>
          <h3>全部任职</h3>
          {visibleMemberships.length ? (
            <ul className="profile-memberships">
              {visibleMemberships.map((membership) => (
                <li key={membership.id}>
                  <strong>{Array.isArray(membership.organization_path) ? membership.organization_path.join(" / ") : membership.organization_path}</strong>
                  <span>{membership.job_title || "职位待完善"} · {membership.is_primary ? "主职" : "兼职"} · {membership.role || "未分配"}</span>
                </li>
              ))}
            </ul>
          ) : <p className="profile-empty">暂无有效任职</p>}
        </section>
      )}

      <section className="profile-card" aria-labelledby="change-password-title">
        <h3 id="change-password-title">修改密码</h3>
        <form className="password-form" onSubmit={submitPassword}>
          {formError && <div className="form-error" role="alert">{formError}</div>}
          <label>
            <span>当前密码</span>
            <input ref={currentPasswordRef} type={showPasswords ? "text" : "password"} autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} />
          </label>
          <label>
            <span>新密码</span>
            <input type={showPasswords ? "text" : "password"} autoComplete="new-password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} />
          </label>
          <label>
            <span>确认新密码</span>
            <input type={showPasswords ? "text" : "password"} autoComplete="new-password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} />
          </label>
          <label className="password-visibility">
            <input type="checkbox" checked={showPasswords} onChange={(event) => setShowPasswords(event.target.checked)} />
            <span>显示密码</span>
          </label>
          <button className="confirm-primary" disabled={submitting || !currentPassword || !newPassword || !confirmPassword}>
            {submitting ? "提交中..." : "修改密码"}
          </button>
        </form>
      </section>
    </div>
  );
}
