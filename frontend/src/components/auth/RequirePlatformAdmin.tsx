import { Outlet } from "react-router-dom";
import { useAuthStore } from "../../stores/authStore";
import { hasPlatformAdminAccess } from "./userAccess";

export function RequirePlatformAdmin() {
  const user = useAuthStore((state) => state.user);
  if (!hasPlatformAdminAccess(user)) {
    return (
      <div className="db-empty-state" role="alert">
        <div><h2>无权限访问</h2><p>此页面仅供平台管理员使用。</p></div>
      </div>
    );
  }
  return <Outlet />;
}
