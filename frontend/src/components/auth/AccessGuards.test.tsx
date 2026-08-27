import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";
import { useAuthStore } from "../../stores/authStore";
import { useOrganizationStore } from "../../stores/organizationStore";
import { RequireDepartmentContext } from "./RequireDepartmentContext";
import { RequireDepartmentAdmin } from "./RequireDepartmentAdmin";
import { RequirePlatformAdmin } from "./RequirePlatformAdmin";

describe("user access guards", () => {
  beforeEach(() => {
    useAuthStore.setState({ user: null, token: null, phase: "anonymous", initializing: false, error: null });
    useOrganizationStore.setState({ currentContext: null });
  });

  it("does not mount a department page without a membership context", () => {
    render(<MemoryRouter initialEntries={["/business"]}><Routes>
      <Route element={<RequireDepartmentContext />}><Route path="/business" element={<div>敏感部门数据</div>} /></Route>
    </Routes></MemoryRouter>);
    expect(screen.queryByText("敏感部门数据")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("请先切换部门");
  });

  it("does not infer platform access from an admin membership in V1", () => {
    useAuthStore.setState({ user: { id: "u1", username: "alice", role: "admin", is_platform_admin: false } });
    render(<MemoryRouter initialEntries={["/platform"]}><Routes>
      <Route element={<RequirePlatformAdmin />}><Route path="/platform" element={<div>平台管理内容</div>} /></Route>
    </Routes></MemoryRouter>);
    expect(screen.queryByText("平台管理内容")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("仅供平台管理员");
  });

  it("allows an explicit platform administrator", () => {
    useAuthStore.setState({ user: { id: "u1", username: "root", is_platform_admin: true } });
    render(<MemoryRouter initialEntries={["/platform"]}><Routes>
      <Route element={<RequirePlatformAdmin />}><Route path="/platform" element={<div>平台管理内容</div>} /></Route>
    </Routes></MemoryRouter>);
    expect(screen.getByText("平台管理内容")).toBeInTheDocument();
  });

  it("allows a department administrator without granting platform access", () => {
    useAuthStore.setState({ user: { id: "u1", username: "alice", is_platform_admin: false } });
    useOrganizationStore.setState({ currentContext: {
      organization_id: "department-a", membership_id: "membership-a", company_id: "company-a",
      organization_level: "department", organization_path: ["甲公司", "研发部"],
      role: "admin", permissions: [],
    } });
    render(<MemoryRouter initialEntries={["/departments"]}><Routes>
      <Route element={<RequireDepartmentAdmin />}><Route path="/departments" element={<div>部门管理内容</div>} /></Route>
    </Routes></MemoryRouter>);
    expect(screen.getByText("部门管理内容")).toBeInTheDocument();
  });

  it("rejects a department viewer from department management", () => {
    useAuthStore.setState({ user: { id: "u1", username: "bob", is_platform_admin: false } });
    useOrganizationStore.setState({ currentContext: {
      organization_id: "department-a", membership_id: "membership-b", company_id: "company-a",
      organization_level: "department", organization_path: ["甲公司", "研发部"],
      role: "viewer", permissions: [],
    } });
    render(<MemoryRouter initialEntries={["/departments"]}><Routes>
      <Route element={<RequireDepartmentAdmin />}><Route path="/departments" element={<div>部门管理内容</div>} /></Route>
    </Routes></MemoryRouter>);
    expect(screen.queryByText("部门管理内容")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("无权管理部门");
  });
});
