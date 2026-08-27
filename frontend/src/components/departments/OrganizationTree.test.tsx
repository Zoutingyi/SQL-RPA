import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { OrganizationUnit } from "../../types/organization";
import { OrganizationTree } from "./OrganizationTree";

const tree: OrganizationUnit[] = [{
  id: "company-a", name: "XX科技公司", level: "company", parent_id: null,
  company_id: "company-a", path: ["XX科技公司"], depth: 1, active: true, sort_order: 0,
  member_count: 20,
  children: [{
    id: "department-rd", name: "研发部", level: "department", parent_id: "company-a",
    company_id: "company-a", path: ["XX科技公司", "研发部"], depth: 2, active: true, sort_order: 0,
    children: [{
      id: "group-project", name: "项目开发小组", level: "group", parent_id: "department-rd",
      company_id: "company-a", path: ["XX科技公司", "研发部", "项目开发小组"], depth: 3, active: true, sort_order: 0,
      children: [{
        id: "individual-pm", name: "张三", job_title: "项目经理", level: "individual", parent_id: "group-project",
        company_id: "company-a", path: ["XX科技公司", "研发部", "项目开发小组", "张三－项目经理"], depth: 4,
        active: true, sort_order: 0, children: [],
      }],
    }],
  }],
}];

describe("OrganizationTree", () => {
  it("renders all four levels after expanding the hierarchy", () => {
    const onToggle = vi.fn();
    const { rerender } = render(<OrganizationTree items={tree} query="" expanded={new Set()} onToggle={onToggle} onSelect={vi.fn()} />);
    expect(screen.getByText("公司")).toBeInTheDocument();
    expect(screen.queryByText("研发部")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "展开XX科技公司" }));
    expect(onToggle).toHaveBeenCalledWith("company-a");
    rerender(<OrganizationTree items={tree} query="" expanded={new Set(["company-a", "department-rd", "group-project"])} onToggle={onToggle} onSelect={vi.fn()} />);

    expect(screen.getByText("部门")).toBeInTheDocument();
    expect(screen.getByText("小组")).toBeInTheDocument();
    expect(screen.getByText("个人")).toBeInTheDocument();
    expect(screen.getByText("张三－项目经理")).toBeInTheDocument();
  });

  it("keeps ancestor paths while searching a nested node", () => {
    render(<OrganizationTree items={tree} query="项目经理" expanded={new Set()} onToggle={vi.fn()} onSelect={vi.fn()} />);
    expect(screen.getByText("XX科技公司")).toBeInTheDocument();
    expect(screen.getByText("研发部")).toBeInTheDocument();
    expect(screen.getByText("张三－项目经理")).toBeInTheDocument();
  });
});
