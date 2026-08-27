import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthUser, LoginResponse } from "../api/auth";

const mocks = vi.hoisted(() => ({
  login: vi.fn(),
  getMe: vi.fn(),
  UserContractError: class extends Error {
    missingFields: string[];
    constructor(missingFields: string[]) {
      super("contract mismatch");
      this.missingFields = missingFields;
    }
  },
}));

vi.mock("../api/auth", () => ({
  login: mocks.login,
  getMe: mocks.getMe,
  UserContractError: mocks.UserContractError,
}));

import { resolveAuthenticationPhase, useAuthStore } from "./authStore";
import { useOrganizationStore } from "./organizationStore";

const user: AuthUser = { id: "user-a", username: "alice", role: "operator" };

describe("authStore organization login bootstrap", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    useAuthStore.setState({ user: null, token: null, phase: "anonymous", initializing: false, error: null });
    useOrganizationStore.setState({
      tree: [], memberships: [], currentContext: null, contextRevision: 0,
      loading: false, switching: false, compatibilityMode: false, error: null,
    });
  });

  it("persists the complete primary membership before the first me request", async () => {
    const response: LoginResponse = {
      access_token: "access-token",
      token_type: "bearer",
      tenant_id: "department-a",
      organization: {
        company_id: "company-a",
        organization_id: "department-a",
        membership_id: "membership-primary",
        organization_level: "department",
        organization_path: "company-a/department-a",
        role: "operator",
        permissions: ["document.read"],
        context_token: "signed-context",
        context_version: 3,
        is_primary: true,
      },
      organization_memberships: [{
        company_id: "company-a",
        organization_id: "department-a",
        membership_id: "membership-primary",
        organization_level: "department",
        organization_path: "company-a/department-a",
        role: "operator",
        is_primary: true,
      }, {
        company_id: "company-a",
        organization_id: "department-b",
        membership_id: "membership-part-time",
        organization_level: "department",
        organization_path: "company-a/department-b",
        role: "viewer",
        is_primary: false,
      }],
      user,
    };
    mocks.login.mockResolvedValue(response);
    mocks.getMe.mockImplementation(async () => {
      expect(localStorage.getItem("sql_rpa_token")).toBe("access-token");
      expect(localStorage.getItem("sql_rpa_organization_id")).toBe("department-a");
      expect(localStorage.getItem("sql_rpa_membership_id")).toBe("membership-primary");
      expect(localStorage.getItem("sql_rpa_organization_context")).toBe("signed-context");
      return user;
    });

    await useAuthStore.getState().login("alice", "password");

    expect(useOrganizationStore.getState().currentContext).toMatchObject({
      organization_id: "department-a",
      membership_id: "membership-primary",
      context_version: 3,
    });
    expect(useOrganizationStore.getState().memberships).toHaveLength(2);
    expect(useAuthStore.getState().phase).toBe("ready");
  });

  it("clears stale department headers when login returns a platform context", async () => {
    localStorage.setItem("sql_rpa_organization_id", "stale-department");
    localStorage.setItem("sql_rpa_membership_id", "stale-membership");
    mocks.login.mockResolvedValue({
      access_token: "platform-token",
      token_type: "bearer",
      organization: null,
      organization_memberships: [],
      user: { ...user, role: "admin" },
    } satisfies LoginResponse);
    mocks.getMe.mockImplementation(async () => {
      expect(localStorage.getItem("sql_rpa_organization_id")).toBeNull();
      expect(localStorage.getItem("sql_rpa_membership_id")).toBeNull();
      return { ...user, role: "admin" };
    });

    await useAuthStore.getState().login("admin", "password");

    expect(useOrganizationStore.getState().currentContext).toBeNull();
  });

  it("retains the token and reports a deployment error on a V1 contract mismatch", async () => {
    localStorage.setItem("sql_rpa_token", "diagnostic-token");
    mocks.getMe.mockRejectedValue(new mocks.UserContractError(["organization_memberships"]));

    await useAuthStore.getState().loadMe();

    expect(localStorage.getItem("sql_rpa_token")).toBe("diagnostic-token");
    expect(useAuthStore.getState()).toMatchObject({
      user: null,
      token: "diagnostic-token",
      phase: "deployment_error",
    });
    expect(useAuthStore.getState().error).toContain("organization_memberships");
  });

  it("enters forced-password phase before any business initialization", async () => {
    mocks.login.mockResolvedValue({
      access_token: "forced-token",
      token_type: "bearer",
      organization: null,
      organization_memberships: [],
      user,
    } satisfies LoginResponse);
    mocks.getMe.mockResolvedValue({
      ...user,
      must_change_password: true,
      is_platform_admin: false,
      current_membership: null,
      organization_memberships: [],
    });

    await useAuthStore.getState().login("alice", "password");

    expect(useAuthStore.getState().phase).toBe("password_change_required");
  });
});

describe("resolveAuthenticationPhase", () => {
  it("allows a platform administrator without a department", () => {
    expect(resolveAuthenticationPhase({
      id: "platform", username: "root", is_platform_admin: true,
      must_change_password: false, current_membership: null, organization_memberships: [],
    }, false)).toBe("ready");
  });

  it("blocks a normal user without a membership", () => {
    expect(resolveAuthenticationPhase({
      id: "user", username: "no-membership", is_platform_admin: false,
      must_change_password: false, current_membership: null, organization_memberships: [],
    }, false)).toBe("membership_unavailable");
  });

  it("does not let platform status bypass forced password change", () => {
    expect(resolveAuthenticationPhase({
      id: "platform", username: "root", is_platform_admin: true,
      must_change_password: true, current_membership: null, organization_memberships: [],
    }, false)).toBe("password_change_required");
  });
});
