import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuthStore } from "../../stores/authStore";
import { useOrganizationStore } from "../../stores/organizationStore";
import { useToastStore } from "../../stores/toastStore";
import type { OrganizationMembership } from "../../types/organization";
import { MembershipAccessRecovery } from "./MembershipAccessRecovery";

vi.mock("./ProfilePage", () => ({ ProfilePage: () => <div>个人设置</div> }));

const membership = (id: string, role: string | null): OrganizationMembership => ({
  id,
  user_id: "user-a",
  organization_id: `department-${id}`,
  organization_level: "department",
  organization_name: `部门${id}`,
  organization_path: ["甲公司", `部门${id}`],
  company_id: "company-a",
  role,
  job_title: "工程师",
  is_primary: id === "primary",
  active: true,
});

describe("MembershipAccessRecovery", () => {
  beforeEach(() => {
    useOrganizationStore.setState({
      memberships: [membership("primary", null), membership("secondary", "approver")],
      currentContext: {
        organization_id: "department-primary", membership_id: "primary", company_id: "company-a",
        organization_level: "department", organization_path: ["甲公司", "部门primary"],
        role: null, permissions: [],
      },
      switching: false,
      error: null,
    });
  });

  it("lets an unassigned current identity switch to another authorized membership", async () => {
    const user = userEvent.setup();
    const switchMembership = vi.fn().mockResolvedValue(true);
    const loadMe = vi.fn().mockResolvedValue(undefined);
    const addToast = vi.fn();
    useOrganizationStore.setState({ switchMembership });
    useAuthStore.setState({ loadMe });
    useToastStore.setState({ addToast });

    render(<MembershipAccessRecovery />);
    await user.selectOptions(screen.getByRole("combobox", { name: "选择其他有效部门任职" }), "secondary");
    await user.click(screen.getByRole("button", { name: "确认切换" }));

    expect(switchMembership).toHaveBeenCalledWith("secondary");
    expect(loadMe).toHaveBeenCalledTimes(1);
    expect(addToast).toHaveBeenCalledWith(expect.objectContaining({ type: "success" }));
  });
});
