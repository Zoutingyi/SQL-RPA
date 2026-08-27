import { useEffect } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { MainLayout } from "./components/layout/MainLayout";
import { ChatPanel } from "./components/chat/ChatPanel";
import { DocumentList } from "./components/documents/DocumentList";
import { SettingsPage } from "./components/settings/SettingsPage";
import { MemoryList } from "./components/memories/MemoryList";
import { DatabasePanel } from "./components/database/DatabasePanel";
import { DbConnectionDialog } from "./components/database/DbConnectionDialog";
import { ToastContainer } from "./components/shared/Toast";
import { ConfirmProvider } from "./components/shared/ConfirmDialog";
import { LoginPage } from "./components/auth/LoginPage";
import { UserManagement } from "./components/auth/UserManagement";
import { UsagePage } from "./components/usage/UsagePage";
import { RequireRole } from "./components/auth/RequireRole";
import { RequireDepartmentContext } from "./components/auth/RequireDepartmentContext";
import { RequirePlatformAdmin } from "./components/auth/RequirePlatformAdmin";
import { RequireDepartmentAdmin } from "./components/auth/RequireDepartmentAdmin";
import { ProfilePage } from "./components/auth/ProfilePage";
import { MembershipAccessRecovery } from "./components/auth/MembershipAccessRecovery";
import { useAuthStore } from "./stores/authStore";
import { OnboardingGuide } from "./components/onboarding/OnboardingGuide";
import { NotificationCenter } from "./components/notifications/NotificationCenter";
import { BillingPage } from "./components/billing/BillingPage";
import { TelemetryReporter } from "./components/shared/TelemetryReporter";
import { DepartmentManagementPage } from "./components/departments/DepartmentManagementPage";
import { useOrganizationStore } from "./stores/organizationStore";

export default function App() {
  const user = useAuthStore((s) => s.user);
  const phase = useAuthStore((s) => s.phase);
  const authError = useAuthStore((s) => s.error);
  const logout = useAuthStore((s) => s.logout);
  const initializing = useAuthStore((s) => s.initializing);
  const loadMe = useAuthStore((s) => s.loadMe);
  const contextRevision = useOrganizationStore((s) => s.contextRevision);
  const loadOrganization = useOrganizationStore((s) => s.loadOrganization);
  const currentContext = useOrganizationStore((s) => s.currentContext);

  useEffect(() => { loadMe(); }, [loadMe]);
  useEffect(() => {
    if (!user || phase !== "ready") return;
    const hasDepartmentIdentity = Boolean(currentContext || user.current_membership || user.membership_id);
    if (user.is_platform_admin === true && !hasDepartmentIdentity) return;
    void loadOrganization(typeof user.role === "string" ? user.role : undefined);
  }, [user?.id, phase, loadOrganization]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const saved = localStorage.getItem("sql_rpa_theme");
    if (saved === "light" || saved === "dark") {
      document.documentElement.setAttribute("data-theme", saved);
    } else {
      const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      document.documentElement.setAttribute("data-theme", prefersDark ? "dark" : "light");
    }
  }, []);

  if (initializing) {
    return (
      <div className="app-loading">
        <div className="typing-dots"><span /><span /><span /></div>
        <span>正在验证登录状态...</span>
      </div>
    );
  }

  return (
    <ConfirmProvider>
      <BrowserRouter>
        <TelemetryReporter />
        {phase === "deployment_error" ? (
          <div className="access-unavailable deployment-error" role="alert">
            <div><h2>前后端部署版本不匹配</h2><p>{authError}</p><p>当前令牌已保留用于排障，系统不会进入业务页面。</p><button className="confirm-primary" onClick={logout}>退出并清理会话</button></div>
          </div>
        ) : user ? (
          phase === "password_change_required" ? (
            <Routes>
              <Route path="/profile" element={<ProfilePage forced />} />
              <Route path="*" element={<Navigate to="/profile?section=password&forced=1" replace />} />
            </Routes>
          ) : phase === "permission_unassigned" ? (
            <Routes>
              <Route path="/profile" element={<MembershipAccessRecovery />} />
              <Route path="*" element={<Navigate to="/profile" replace />} />
            </Routes>
          ) : phase === "membership_unavailable" ? (
            <div className="access-unavailable" role="alert">
              <div><h2>账号暂无有效任职</h2><p>请联系管理员分配部门任职和权限后重试。</p><button className="confirm-primary" onClick={logout}>退出登录</button></div>
            </div>
          ) : (
            <>
              {currentContext && <DbConnectionDialog key={`database-dialog-${contextRevision}`} />}
              {currentContext && <OnboardingGuide />}
              <Routes>
                <Route element={<MainLayout key={contextRevision} />}>
                  <Route path="/profile" element={<ProfilePage />} />
                  <Route element={<RequireDepartmentContext />}>
                    <Route path="/" element={<ChatPanel />} />
                    <Route path="/documents" element={<DocumentList />} />
                    <Route path="/database" element={<DatabasePanel />} />
                    <Route path="/memories" element={<MemoryList />} />
                    <Route path="/notifications" element={<NotificationCenter />} />
                    <Route element={<RequireRole roles={["approver", "admin"]} />}>
                      <Route path="/usage" element={<UsagePage />} />
                      <Route path="/billing" element={<BillingPage />} />
                    </Route>
                  </Route>
                  <Route element={<RequirePlatformAdmin />}>
                    <Route path="/settings" element={<SettingsPage />} />
                    <Route path="/users" element={<UserManagement />} />
                  </Route>
                  <Route element={<RequireDepartmentAdmin />}>
                    <Route path="/departments" element={<DepartmentManagementPage />} />
                    <Route path="/tenants" element={<Navigate to="/departments" replace />} />
                  </Route>
                  <Route path="*" element={<Navigate to={currentContext ? "/" : "/profile"} replace />} />
                </Route>
              </Routes>
            </>
          )
        ) : (
          <LoginPage />
        )}
      </BrowserRouter>
      <ToastContainer />
    </ConfirmProvider>
  );
}
