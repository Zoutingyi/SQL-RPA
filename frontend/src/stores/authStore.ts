import { create } from "zustand";
import { getMe, login as loginApi, UserContractError, type AuthUser, type LoginResponse } from "../api/auth";
import { abortOrganizationRequests, ApiError, formatApiError } from "../api/client";
import { useOrganizationStore } from "./organizationStore";
import type { OrganizationContext, OrganizationMembership } from "../types/organization";

const TOKEN_KEY = "sql_rpa_token";

interface AuthState {
  user: AuthUser | null;
  token: string | null;
  phase: AuthenticationPhase;
  initializing: boolean;
  error: string | null;
  loadMe: () => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  clearError: () => void;
}

export type AuthenticationPhase =
  | "anonymous"
  | "authenticating"
  | "deployment_error"
  | "password_change_required"
  | "permission_unassigned"
  | "ready"
  | "membership_unavailable";

function readToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function resolveAuthenticationPhase(user: AuthUser, hasOrganizationContext: boolean): AuthenticationPhase {
  if (user.must_change_password === true) return "password_change_required";
  if (user.is_platform_admin === true) return "ready";
  if (user.current_membership && user.current_membership.role === null) return "permission_unassigned";
  if (user.current_membership || user.membership_id || hasOrganizationContext) return "ready";
  if (user.is_platform_admin === false || user.current_membership === null || user.organization_memberships) {
    return "membership_unavailable";
  }
  return "deployment_error";
}

function deploymentError(error: UserContractError): string {
  return `部署契约错误：/api/auth/me 缺少或错误的字段 ${error.missingFields.join("、")}。请联系系统管理员核对前后端版本。`;
}

function clearSession(): void {
  abortOrganizationRequests();
  localStorage.removeItem(TOKEN_KEY);
  useOrganizationStore.getState().clear();
}

function normalizePath(path: string[] | string): string[] {
  return Array.isArray(path) ? path : path.split("/").filter(Boolean);
}

function loginOrganizationContext(result: LoginResponse): OrganizationContext | null {
  const organization = result.organization;
  if (!organization) return null;
  return {
    company_id: organization.company_id,
    organization_id: organization.organization_id,
    membership_id: organization.membership_id,
    organization_level: organization.organization_level,
    organization_path: normalizePath(organization.organization_path),
    role: organization.role,
    permissions: organization.permissions ?? [],
    context_token: organization.context_token,
    context_version: organization.context_version,
    expires_at: organization.expires_at,
  };
}

function loginMemberships(result: LoginResponse): OrganizationMembership[] {
  return (result.organization_memberships || []).map((membership) => ({
    id: membership.membership_id,
    user_id: result.user.id,
    organization_id: membership.organization_id,
    organization_level: membership.organization_level,
    organization_name: membership.organization_name || normalizePath(membership.organization_path).at(-1) || "",
    organization_path: normalizePath(membership.organization_path),
    company_id: membership.company_id,
    role: membership.role,
    permissions: membership.permissions,
    job_title: membership.job_title,
    is_primary: membership.is_primary,
    active: membership.active ?? true,
    valid_from: membership.valid_from,
    valid_to: membership.valid_to,
    version: membership.version,
  }));
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  token: readToken(),
  phase: readToken() ? "authenticating" : "anonymous",
  initializing: true,
  error: null,

  loadMe: async () => {
    set({ initializing: true, error: null });
    try {
      const user = await getMe();
      if (user.current_membership) {
        useOrganizationStore.getState().refreshCurrentRole(user.current_membership.role);
      }
      const phase = resolveAuthenticationPhase(user, Boolean(useOrganizationStore.getState().currentContext));
      set({ user, token: readToken(), phase, initializing: false });
    } catch (error) {
      if (error instanceof UserContractError) {
        set({ user: null, token: readToken(), phase: "deployment_error", initializing: false, error: deploymentError(error) });
        return;
      }
      clearSession();
      set({ user: null, token: null, phase: "anonymous", initializing: false });
    }
  },

  login: async (username, password) => {
    set({ error: null, phase: "authenticating" });
    try {
      const result = await loginApi(username, password);
      localStorage.setItem(TOKEN_KEY, result.access_token);
      useOrganizationStore.getState().bootstrapLoginContext(
        loginOrganizationContext(result),
        loginMemberships(result),
      );
      const user = await getMe();
      if (user.current_membership) {
        useOrganizationStore.getState().refreshCurrentRole(user.current_membership.role);
      }
      const phase = resolveAuthenticationPhase(user, Boolean(useOrganizationStore.getState().currentContext));
      set({ user, token: result.access_token, phase });
    } catch (e) {
      if (e instanceof UserContractError) {
        set({ user: null, token: readToken(), phase: "deployment_error", error: deploymentError(e) });
        throw e;
      }
      clearSession();
      const detail = e instanceof ApiError && e.status === 403
        ? e.code === "PASSWORD_CHANGE_REQUIRED"
          ? "首次登录需要先修改密码"
          : "该账号当前无法登录；如为平台管理员，请后端确认已允许无部门登录"
        : formatApiError(e, "登录失败");
      set({ user: null, token: null, phase: "anonymous", error: detail });
      throw e;
    }
  },

  logout: () => {
    clearSession();
    set({ user: null, token: null, phase: "anonymous", error: null });
  },

  clearError: () => set({ error: null }),
}));

if (typeof window !== "undefined") {
  window.addEventListener("sql_rpa:unauthorized", () => {
    clearSession();
    useAuthStore.setState({ user: null, token: null, phase: "anonymous" });
  });
}
