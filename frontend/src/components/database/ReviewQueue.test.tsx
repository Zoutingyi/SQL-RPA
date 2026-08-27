import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ReviewQueue } from "./ReviewQueue";
import type { ReviewTask } from "../../types";

const { listReviews, getReview, addToast } = vi.hoisted(() => ({
  listReviews: vi.fn(),
  getReview: vi.fn(),
  addToast: vi.fn(),
}));

vi.mock("../../api/database", () => ({ listReviews, getReview }));
vi.mock("../../stores/toastStore", () => ({
  useToastStore: (selector: (state: { addToast: typeof addToast }) => unknown) => selector({ addToast }),
}));

function task(status: string): ReviewTask {
  return {
    id: "review-1", sql: "DELETE FROM users WHERE id = 1", reason: "",
    operation_type: "DELETE", affected_table: "users", affected_rows: 1,
    columns: [], preview_rows: [], has_backup: true, backup_id: "backup-1",
    safety_level: "danger", safety_message: "", warnings: [], status,
    submitted_by: "alice", created_at: "2026-08-21T00:00:00Z",
  };
}

describe("ReviewQueue", () => {
  beforeEach(() => vi.clearAllMocks());

  it("loads and exposes committed operations whose records need recovery", async () => {
    const pending = task("executed_record_pending");
    listReviews.mockResolvedValue({ items: [pending], total: 1 });
    getReview.mockResolvedValue(pending);
    const onOpen = vi.fn();

    render(<ReviewQueue refreshToken={0} onOpen={onOpen} />);

    expect(await screen.findByText("业务已执行·记录待恢复")).toBeInTheDocument();
    expect(listReviews).toHaveBeenCalledWith(
      "awaiting_review,pending_second_approval,executing,executed_record_pending",
    );
    await userEvent.click(screen.getByText("打开审核"));
    await waitFor(() => expect(onOpen).toHaveBeenCalledWith(pending));
  });
});
