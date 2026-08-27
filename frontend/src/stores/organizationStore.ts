import { create } from "zustand";
import { abortOrganizationRequests, isOrganizationRequestCancelled } from "../api/client";
import { getDepartmentTree, getMyMemberships, switchDepartmentContext } from "../api/departments";
import { formatApiError } from "../api/client";
import type { OrganizationContext, OrganizationMembership, OrganizationUnit } from "../types/organization";
import { resetOrganizationScope } from "./organizationScope";

export const ORGANIZATION_ID_KEY = "sql_rpa_organization_id";
export const MEMBERSHIP_ID_KEY = "sql_rpa_membership_id";
export const CONTEXT_TOKEN_KEY = "sql_rpa_organization_context";
export const TENANT_KEY = "sql_rpa_tenant_id";

interface OrganizationState {
  tree: OrganizationUnit[];
  memberships: OrganizationMembership[];
  currentContext: OrganizationContext | null;
  contextRevision: number;
  loading: boolean;
  switching: boolean;
  compatibilityMode: boolean;
  error: string | null;
  loadOrganization: (activeRole?: string) => Promise<void>;
  bootstrapLoginContext: (
    context: OrganizationContext | null,
    memberships: OrganizationMembership[],
  ) => void;
  switchMembership: (membershipId: string) => Promise<boolean>;
  refreshCurrentRole: (role: string | null) => void;
  clear: () => void;
}

function storeContext(context: OrganizationContext): void {
  localStorage.setItem(ORGANIZATION_ID_KEY, context.organization_id);
  localStorage.setItem(MEMBERSHIP_ID_KEY, context.membership_id);
  localStorage.setItem(TENANT_KEY, context.organization_id);
  if (context.context_token) localStorage.setItem(CONTEXT_TOKEN_KEY, context.context_token);
  else localStorage.removeItem(CONTEXT_TOKEN_KEY);
}

function removeStoredContext(): void {
  localStorage.removeItem(ORGANIZATION_ID_KEY);
  localStorage.removeItem(MEMBERSHIP_ID_KEY);
  localStorage.removeItem(CONTEXT_TOKEN_KEY);
  localStorage.removeItem(TENANT_KEY);
}

function contextFromMembership(membership: OrganizationMembership): OrganizationContext {
  return {
    organization_id: membership.organization_id,
    membership_id: membership.id,
    company_id: membership.company_id,
    organization_level: membership.organization_level,
    organization_path: membership.organization_path,
    role: membership.role,
    permissions: membership.permissions ?? [],
    compatibility_mode: membership.compatibility_mode,
  };
}

function isMembershipCurrentlyValid(membership: OrganizationMembership, now = Date.now()): boolean {
  if (!membership.active) return false;
  const validFrom = membership.valid_from ? Date.parse(membership.valid_from) : Number.NaN;
  const validTo = membership.valid_to ? Date.parse(membership.valid_to) : Number.NaN;
  if (!Number.isNaN(validFrom) && validFrom > now) return false;
  if (!Number.isNaN(validTo) && validTo <= now) return false;
  return true;
}

function collectOrganizationPaths(
  units: OrganizationUnit[],
  result = new Map<string, string[]>(),
): Map<string, string[]> {
  for (const unit of units) {
    result.set(unit.id, unit.path);
    collectOrganizationPaths(unit.children, result);
  }
  return result;
}

export const useOrganizationStore = create<OrganizationState>((set, get) => ({
  tree: [],
  memberships: [],
  currentContext: null,
  contextRevision: 0,
  loading: false,
  switching: false,
  compatibilityMode: false,
  error: null,

  bootstrapLoginContext: (context, memberships) => {
    abortOrganizationRequests();
    resetOrganizationScope();
    if (context) storeContext(context);
    else removeStoredContext();
    set((state) => ({
      tree: [],
      memberships,
      currentContext: context,
      contextRevision: state.contextRevision + 1,
      loading: false,
      switching: false,
      compatibilityMode: false,
      error: null,
    }));
  },

  loadOrganization: async (_activeRole = "viewer") => {
    set({ loading: true, error: null });
    try {
      const [treeResult, membershipResult] = await Promise.all([
        getDepartmentTree(),
        getMyMemberships(),
      ]);
      const organizationPaths = collectOrganizationPaths(treeResult.items);
      const memberships = membershipResult.items
        .filter((item) => isMembershipCurrentlyValid(item))
        .map((item) => {
          const path = organizationPaths.get(item.organization_id);
          return path ? {
            ...item,
            organization_path: path,
            organization_name: path.at(-1) || item.organization_name,
          } : item;
        });
      const savedMembershipId = localStorage.getItem(MEMBERSHIP_ID_KEY);
      const savedOrganizationId = localStorage.getItem(ORGANIZATION_ID_KEY) || localStorage.getItem(TENANT_KEY);
      const previousContext = get().currentContext;
      const previousStillValid = previousContext
        ? memberships.some((item) => item.id === previousContext.membership_id)
        : true;
      const selected = previousStillValid
        ? memberships.find((item) => item.id === savedMembershipId)
          || memberships.find((item) => item.organization_id === savedOrganizationId)
          || memberships.find((item) => item.is_primary)
        : undefined;
      const currentContext = selected ? {
        ...contextFromMembership(selected),
        ...(previousContext?.membership_id === selected.id ? {
          context_token: previousContext.context_token,
          context_version: previousContext.context_version,
          expires_at: previousContext.expires_at,
          permissions: previousContext.permissions,
        } : {}),
      } : null;
      if (!previousStillValid) {
        abortOrganizationRequests();
        resetOrganizationScope();
      }
      if (currentContext) storeContext(currentContext);
      else removeStoredContext();
      set((state) => ({
        tree: treeResult.items,
        memberships,
        currentContext,
        compatibilityMode: !!(treeResult.compatibility_mode || membershipResult.compatibility_mode),
        loading: false,
        contextRevision: previousStillValid ? state.contextRevision : state.contextRevision + 1,
        error: !previousStillValid
          ? "当前任职已失效，已清除旧部门数据，请重新选择有效身份"
          : (!currentContext && memberships.length > 0
            ? "账号尚未配置主职，系统不会自动启用任意兼职，请联系管理员完成主职配置"
            : null),
      }));
    } catch (error) {
      if (isOrganizationRequestCancelled(error)) return;
      set({ loading: false, error: formatApiError(error, "加载部门信息失败") });
    }
  },

  switchMembership: async (membershipId) => {
    if (get().switching || membershipId === get().currentContext?.membership_id) return false;
    const membership = get().memberships.find((item) => item.id === membershipId && item.active);
    if (!membership) {
      set({ error: "该任职关系已失效，请刷新部门列表" });
      return false;
    }
    set({ switching: true, error: null });
    try {
      const context = await switchDepartmentContext(membership);
      abortOrganizationRequests();
      resetOrganizationScope();
      storeContext(context);
      set((state) => ({
        currentContext: context,
        contextRevision: state.contextRevision + 1,
        switching: false,
      }));
      window.dispatchEvent(new CustomEvent("sql_rpa:organization-changed", {
        detail: { organizationId: context.organization_id, membershipId: context.membership_id },
      }));
      return true;
    } catch (error) {
      if (isOrganizationRequestCancelled(error)) return false;
      set({ switching: false, error: formatApiError(error, "切换部门失败") });
      if (error instanceof Error && "status" in error && (error as { status?: number }).status === 409) {
        await get().loadOrganization();
      }
      return false;
    }
  },

  refreshCurrentRole: (role) => set((state) => state.currentContext ? {
    currentContext: { ...state.currentContext, role },
  } : state),

  clear: () => {
    abortOrganizationRequests();
    resetOrganizationScope();
    removeStoredContext();
    set((state) => ({
      tree: [], memberships: [], currentContext: null, compatibilityMode: false,
      contextRevision: state.contextRevision + 1, loading: false, switching: false, error: null,
    }));
  },
}));
