import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiPut: vi.fn(),
}));

vi.mock("./client", () => mocks);

import {
  disableDepartmentMember,
  getDepartmentTree,
  getMyMemberships,
  setPrimaryDepartmentMember,
  updateDepartmentMember,
} from "./departments";

describe("department API contract", () => {
  beforeEach(() => vi.clearAllMocks());

  it("builds a four-level tree from the backend flat node list", async () => {
    mocks.apiGet.mockResolvedValue({ items: [
      { id: "company", name: "甲公司", level: "company", parent_id: null, company_id: "company", depth: 1, sort_order: 0 },
      { id: "department", name: "研发部", level: "department", parent_id: "company", company_id: "company", depth: 2, sort_order: 0 },
      { id: "group", name: "平台组", level: "group", parent_id: "department", company_id: "company", depth: 3, sort_order: 0 },
      { id: "person", name: "张三", level: "individual", parent_id: "group", company_id: "company", depth: 4, sort_order: 0 },
    ] });

    const result = await getDepartmentTree();

    expect(result.items[0].children[0].children[0].children[0]).toMatchObject({
      id: "person",
      path: ["甲公司", "研发部", "平台组", "张三"],
    });
  });

  it("normalizes the backend membership path without using legacy tenant APIs", async () => {
    mocks.apiGet.mockResolvedValue({ items: [{
      id: "membership-a",
      user_id: "user-a",
      organization_id: "department-a",
      organization_level: "department",
      company_id: "company-a",
      path: "company-a/department-a",
      organization_name: "研发部",
      role: "admin",
      is_primary: true,
      active: true,
    }] });

    const result = await getMyMemberships();

    expect(mocks.apiGet).toHaveBeenCalledWith("/api/departments/memberships/me");
    expect(result.items[0]).toMatchObject({
      id: "membership-a",
      organization_path: ["company-a", "department-a"],
      is_primary: true,
    });
  });

  it("surfaces a missing department endpoint instead of silently downgrading", async () => {
    const missing = new Error("404 department endpoint missing");
    mocks.apiGet.mockRejectedValue(missing);

    await expect(getDepartmentTree()).rejects.toBe(missing);
    expect(mocks.apiGet).toHaveBeenCalledTimes(1);
    expect(mocks.apiGet).toHaveBeenCalledWith("/api/departments/tree");
  });

  it("uses the frozen membership management endpoints and preserves concurrency versions", async () => {
    mocks.apiPut.mockResolvedValue({
      id: "membership-a", user_id: "user-a", organization_id: "department-a",
      organization_name: "研发部", organization_level: "department", company_id: "company-a",
      path: ["甲公司", "研发部"], role: "approver", job_title: "负责人",
      is_primary: false, active: true, version: 4,
    });
    mocks.apiPost
      .mockResolvedValueOnce({ old_membership_id: "membership-old", new_membership_id: "membership-a" })
      .mockResolvedValueOnce({ id: "membership-a", active: false });

    const updated = await updateDepartmentMember("membership-a", {
      role: "approver", job_title: "负责人", version: 3,
    });
    await setPrimaryDepartmentMember("membership-a", "岗位调整");
    await disableDepartmentMember("membership-a", "人员离岗");

    expect(updated).toMatchObject({ id: "membership-a", version: 4, organization_path: ["甲公司", "研发部"] });
    expect(mocks.apiPut).toHaveBeenCalledWith("/api/departments/memberships/membership-a", {
      role: "approver", job_title: "负责人", version: 3,
    });
    expect(mocks.apiPost).toHaveBeenNthCalledWith(1, "/api/departments/memberships/membership-a/set-primary", { reason: "岗位调整" });
    expect(mocks.apiPost).toHaveBeenNthCalledWith(2, "/api/departments/memberships/membership-a/disable", {
      replacement_primary_id: null, reason: "人员离岗",
    });
  });
});
