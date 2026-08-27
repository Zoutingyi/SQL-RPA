import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OperationLog } from "./OperationLog";

const { rollbackOperation, loadOperations, addToast } = vi.hoisted(() => ({
  rollbackOperation: vi.fn(),
  loadOperations: vi.fn().mockResolvedValue(undefined),
  addToast: vi.fn(),
}));

vi.mock("../../api/database", () => ({ rollbackOperation }));
vi.mock("../../stores/databaseStore", () => ({
  useDatabaseStore: () => ({
    loading: false,
    loadOperations,
    operations: [{
      id: "operation-1", operation_type: "DELETE", sql_text: "DELETE FROM users WHERE id = 1",
      affected_rows: 1, table_name: "users", backup_id: "backup-1", status: "completed",
      submitted_by: "alice", approved_by: "bob", created_at: "2026-08-21T00:00:00Z",
    }],
  }),
}));
vi.mock("../../stores/toastStore", () => ({
  useToastStore: (selector: (state: { addToast: typeof addToast }) => unknown) => selector({ addToast }),
}));

describe("OperationLog rollback", () => {
  beforeEach(() => vi.clearAllMocks());

  it("does not report success when the reverse backup is missing", async () => {
    rollbackOperation.mockResolvedValue({
      backup_id: "backup-1", reverse_backup_id: null,
      status: "rolled_back", restored_rows: 1,
    });
    const user = userEvent.setup();
    render(<OperationLog compact />);

    await user.click(screen.getByText("回滚"));
    await user.type(screen.getByPlaceholderText("请说明本次回滚原因，便于审计追踪"), "发现错误数据");
    await user.click(screen.getByText("确认回滚"));

    expect(await screen.findByText("回滚未完成")).toBeInTheDocument();
    expect(screen.getByText(/反向备份 缺失/)).toBeInTheDocument();
    expect(addToast).toHaveBeenCalledWith(expect.objectContaining({ type: "error" }));
    expect(addToast).not.toHaveBeenCalledWith(expect.objectContaining({ type: "success" }));
    await waitFor(() => expect(loadOperations).toHaveBeenCalled());
  });
});
