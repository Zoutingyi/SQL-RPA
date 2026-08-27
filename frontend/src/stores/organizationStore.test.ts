import { beforeEach, describe, expect, it, vi } from "vitest";
import type { OrganizationContext, OrganizationMembership, OrganizationUnit } from "../types/organization";

const mocks = vi.hoisted(() => ({
  getDepartmentTree: vi.fn(),
  getMyMemberships: vi.fn(),
  switchDepartmentContext: vi.fn(),
  abortOrganizationRequests: vi.fn(),
}));

vi.mock("../api/departments", () => ({
  getDepartmentTree: mocks.getDepartmentTree,
  getMyMemberships: mocks.getMyMemberships,
  switchDepartmentContext: mocks.switchDepartmentContext,
}));

vi.mock("../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/client")>();
  return { ...original, abortOrganizationRequests: mocks.abortOrganizationRequests };
});

import { registerOrganizationReset } from "./organizationScope";
import { useOrganizationStore } from "./organizationStore";

const unit = (id: string, name: string): OrganizationUnit => ({
  id, name, level: "department", parent_id: "company-a", company_id: "company-a",
  path: ["公司A", name], depth: 2, active: true, sort_order: 0, children: [],
});

const membership = (id: string, organizationId: string, name: string, primary: boolean): OrganizationMembership => ({
  id, user_id: "user-a", organization_id: organizationId, organization_level: "department",
  organization_name: name, organization_path: ["公司A", name], company_id: "company-a",
  role: primary ? "admin" : "approver", is_primary: primary, active: true,
});

describe("organizationStore", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    useOrganizationStore.setState({
      tree: [], memberships: [], currentContext: null, contextRevision: 0,
      loading: false, switching: false, compatibilityMode: false, error: null,
    });
  });

  it("selects the backend primary membership during initialization", async () => {
    mocks.getDepartmentTree.mockResolvedValue({ items: [unit("department-a", "研发部")] });
    mocks.getMyMemberships.mockResolvedValue({ items: [membership("membership-a", "department-a", "研发部", true)] });

    await useOrganizationStore.getState().loadOrganization("admin");

    expect(useOrganizationStore.getState().currentContext).toMatchObject({
      organization_id: "department-a", membership_id: "membership-a", role: "admin",
    });
    expect(localStorage.getItem("sql_rpa_organization_id")).toBe("department-a");
    expect(localStorage.getItem("sql_rpa_membership_id")).toBe("membership-a");
  });

  it("preserves the signed login context while refreshing the same membership", async () => {
    const primary = membership("membership-a", "department-a", "研发部", true);
    useOrganizationStore.setState({ currentContext: {
      organization_id: "department-a", membership_id: "membership-a", company_id: "company-a",
      organization_level: "department", organization_path: ["公司A", "研发部"],
      role: "admin", permissions: ["organization.manage"], context_token: "signed-login-context",
      context_version: 7, expires_at: "2026-08-25T01:00:00Z",
    } });
    mocks.getDepartmentTree.mockResolvedValue({ items: [unit("department-a", "研发部")] });
    mocks.getMyMemberships.mockResolvedValue({ items: [primary] });

    await useOrganizationStore.getState().loadOrganization("admin");

    expect(useOrganizationStore.getState().currentContext).toMatchObject({
      membership_id: "membership-a",
      context_token: "signed-login-context",
      context_version: 7,
      permissions: ["organization.manage"],
    });
    expect(localStorage.getItem("sql_rpa_organization_context")).toBe("signed-login-context");
  });

  it("clears the old scope and commits the new context only after switch succeeds", async () => {
    const primary = membership("membership-a", "department-a", "研发部", true);
    const secondary = membership("membership-b", "department-b", "项目部", false);
    const nextContext: OrganizationContext = {
      organization_id: "department-b", membership_id: "membership-b", company_id: "company-a",
      organization_level: "department", organization_path: ["公司A", "项目部"],
      role: "approver", permissions: ["review.read"], context_token: "context-b",
    };
    useOrganizationStore.setState({ memberships: [primary, secondary], currentContext: {
      organization_id: "department-a", membership_id: "membership-a", company_id: "company-a",
      organization_level: "department", organization_path: ["公司A", "研发部"], role: "admin", permissions: [],
    } });
    const reset = vi.fn();
    const unregister = registerOrganizationReset("organization-store-test", reset);
    mocks.switchDepartmentContext.mockResolvedValue(nextContext);

    const result = await useOrganizationStore.getState().switchMembership("membership-b");

    expect(result).toBe(true);
    expect(mocks.switchDepartmentContext).toHaveBeenCalledWith(secondary);
    expect(mocks.abortOrganizationRequests).toHaveBeenCalledTimes(1);
    expect(reset).toHaveBeenCalledTimes(1);
    expect(useOrganizationStore.getState().currentContext).toEqual(nextContext);
    expect(useOrganizationStore.getState().contextRevision).toBe(1);
    expect(localStorage.getItem("sql_rpa_organization_context")).toBe("context-b");
    unregister();
  });

  it("keeps the current context when switching fails", async () => {
    const primary = membership("membership-a", "department-a", "研发部", true);
    const secondary = membership("membership-b", "department-b", "项目部", false);
    useOrganizationStore.setState({ memberships: [primary, secondary], currentContext: {
      organization_id: "department-a", membership_id: "membership-a", company_id: "company-a",
      organization_level: "department", organization_path: ["公司A", "研发部"], role: "admin", permissions: [],
    } });
    mocks.switchDepartmentContext.mockRejectedValue(new Error("switch failed"));

    const result = await useOrganizationStore.getState().switchMembership("membership-b");

    expect(result).toBe(false);
    expect(mocks.abortOrganizationRequests).not.toHaveBeenCalled();
    expect(useOrganizationStore.getState().currentContext?.membership_id).toBe("membership-a");
  });

  it("does not silently activate an arbitrary part-time membership when no primary exists", async () => {
    const secondary = membership("membership-b", "department-b", "项目部", false);
    mocks.getDepartmentTree.mockResolvedValue({ items: [unit("department-b", "项目部")] });
    mocks.getMyMemberships.mockResolvedValue({ items: [secondary] });

    await useOrganizationStore.getState().loadOrganization();

    expect(useOrganizationStore.getState().memberships).toHaveLength(1);
    expect(useOrganizationStore.getState().currentContext).toBeNull();
    expect(useOrganizationStore.getState().error).toContain("尚未配置主职");
    expect(localStorage.getItem("sql_rpa_membership_id")).toBeNull();
  });

  it("keeps an explicitly selected valid part-time membership on refresh", async () => {
    const secondary = membership("membership-b", "department-b", "项目部", false);
    localStorage.setItem("sql_rpa_membership_id", secondary.id);
    localStorage.setItem("sql_rpa_organization_id", secondary.organization_id);
    mocks.getDepartmentTree.mockResolvedValue({ items: [unit("department-b", "项目部")] });
    mocks.getMyMemberships.mockResolvedValue({ items: [secondary] });

    await useOrganizationStore.getState().loadOrganization();

    expect(useOrganizationStore.getState().currentContext?.membership_id).toBe("membership-b");
  });

  it("clears scoped state instead of switching identities when the current membership becomes invalid", async () => {
    const primary = membership("membership-a", "department-a", "研发部", true);
    const replacement = membership("membership-b", "department-b", "项目部", true);
    localStorage.setItem("sql_rpa_membership_id", primary.id);
    localStorage.setItem("sql_rpa_organization_id", primary.organization_id);
    useOrganizationStore.setState({ memberships: [primary], currentContext: {
      organization_id: primary.organization_id, membership_id: primary.id, company_id: primary.company_id,
      organization_level: primary.organization_level, organization_path: primary.organization_path,
      role: primary.role, permissions: [],
    } });
    mocks.getDepartmentTree.mockResolvedValue({ items: [unit("department-b", "项目部")] });
    mocks.getMyMemberships.mockResolvedValue({ items: [replacement] });
    const reset = vi.fn();
    const unregister = registerOrganizationReset("invalid-membership-test", reset);

    await useOrganizationStore.getState().loadOrganization();

    expect(mocks.abortOrganizationRequests).toHaveBeenCalledTimes(1);
    expect(reset).toHaveBeenCalledTimes(1);
    expect(useOrganizationStore.getState().currentContext).toBeNull();
    expect(useOrganizationStore.getState().error).toContain("当前任职已失效");
    expect(localStorage.getItem("sql_rpa_membership_id")).toBeNull();
    unregister();
  });
});
