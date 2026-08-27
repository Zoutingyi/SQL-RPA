import { Outlet } from "react-router-dom";
import { useAuthStore } from "../../stores/authStore";
import { useOrganizationStore } from "../../stores/organizationStore";

export function RequireRole({ roles }: { roles: string[] }) {
  const user = useAuthStore((s) => s.user);
  const context = useOrganizationStore((s) => s.currentContext);
  const effectiveRole = context ? context.role : user?.role;

  if (!user || !effectiveRole || !roles.includes(effectiveRole)) {
    return <div className="db-empty-state" role="alert"><div><h2>无权限访问</h2><p>当前部门角色为 {effectiveRole || "未登录"}，此页面需要 {roles.join(" / ")} 权限。</p><p>如需访问，请联系当前部门管理员调整任职角色。</p></div></div>;
  }

  return <Outlet />;
}
