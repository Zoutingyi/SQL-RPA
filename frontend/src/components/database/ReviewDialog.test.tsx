import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { ReviewDialog } from "./ReviewDialog";
import { useAuthStore } from "../../stores/authStore";
import type { ReviewTask } from "../../types";

function makeTask(overrides: Partial<ReviewTask> = {}): ReviewTask {
  return {
    id: "task-1",
    sql: "DELETE FROM users WHERE id = 1",
    reason: "test",
    operation_type: "DELETE",
    affected_table: "users",
    affected_rows: 1,
    columns: ["id", "name"],
    preview_columns: ["id", "name"],
    preview_rows: [[1, "Alice"]],
    has_backup: true,
    backup_id: null,
    safety_level: "danger",
    safety_message: "This DELETE operation requires review.",
    warnings: [],
    status: "awaiting_review",
    submitted_by: "alice",
    approved_by: "",
    reviewed_at: "",
    created_at: "2026-08-18T00:00:00Z",
    ...overrides,
  };
}

describe("ReviewDialog", () => {
  it("disables self-approval and shows a warning", () => {
    useAuthStore.setState({
      user: { id: "alice", username: "alice", role: "approver" },
    });

    render(
      <ReviewDialog
        open
        task={makeTask()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText(/提交人与审批人不能是同一个人/)).toBeInTheDocument();
    expect(screen.getByText("确认执行").closest("button")).toBeDisabled();
  });

  it("closes without calling the reject API", () => {
    const onClose = vi.fn();
    const onReject = vi.fn();

    render(
      <ReviewDialog
        open
        task={makeTask({ submitted_by: "bob" })}
        onApprove={vi.fn()}
        onReject={onReject}
        onClose={onClose}
      />,
    );

    fireEvent.click(screen.getByText("关闭"));
    expect(onClose).toHaveBeenCalled();
    expect(onReject).not.toHaveBeenCalled();
  });

  it("shows the second-approver state and blocks the first approver", () => {
    useAuthStore.setState({
      user: { id: "alice", username: "alice", role: "approver" },
    });

    render(
      <ReviewDialog
        open
        task={makeTask({
          submitted_by: "bob",
          status: "pending_second_approval",
          approved_by: "alice",
          first_approver_id: "alice",
        })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText(/需由第二名审批人批准/)).toBeInTheDocument();
    expect(screen.getByText("审批时间线")).toBeInTheDocument();
    expect(screen.getByText(/第一人已批准/)).toBeInTheDocument();
    expect(screen.getByText("后端风险评分")).toBeInTheDocument();
    expect(screen.getByText("批准并执行").closest("button")).toBeDisabled();
  });

  it("locks execution when business SQL committed but records need recovery", () => {
    useAuthStore.setState({
      user: { id: "carol", username: "carol", role: "approver" },
    });

    render(
      <ReviewDialog
        open
        task={makeTask({ submitted_by: "bob", status: "executed_record_pending" })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText(/业务 SQL 已执行，审核\/审计记录待恢复/)).toBeInTheDocument();
    expect(screen.getByText("确认执行").closest("button")).toBeDisabled();
    expect(screen.queryByText("拒绝")).not.toBeInTheDocument();
    expect(screen.queryByText(/数据库未修改/)).not.toBeInTheDocument();
  });

  it("lets an admin repair records without exposing the execute action", async () => {
    useAuthStore.setState({
      user: { id: "admin-1", username: "admin", role: "admin" },
    });
    const onRecover = vi.fn().mockResolvedValue(undefined);

    render(
      <ReviewDialog
        open
        task={makeTask({ submitted_by: "bob", status: "executed_record_pending" })}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onRecover={onRecover}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("确认执行").closest("button")).toBeDisabled();
    await userEvent.click(screen.getByText("恢复审核/审计记录"));
    await waitFor(() => expect(onRecover).toHaveBeenCalledOnce());
  });
});
