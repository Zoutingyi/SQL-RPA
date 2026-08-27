import { Outlet } from "react-router-dom";
import { useAuthStore } from "../../stores/authStore";
import { useOrganizationStore } from "../../stores/organizationStore";
import { hasPlatformAdminAccess } from "./userAccess";

export function RequireDepartmentAdmin() {
  const user = useAuthStore((state) => state.user);
  const context = useOrganizationStore((state) => state.currentContext);
  const allowed = hasPlatformAdminAccess(user)
    || context?.role === "admin"
    || context?.permissions.includes("organization.manage") === true;

  if (!allowed) {
    return (
      <div className="db-empty-state" role="alert">
        <div>
          <h2>无权管理部门</h2>
          <p>部门管理需要平台管理员身份，或当前任职具备部门管理权限。</p>
        </div>
      </div>
    );
  }

  return <Outlet />;
}
