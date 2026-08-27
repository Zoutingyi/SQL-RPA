import { beforeEach, describe, expect, it, vi } from "vitest";
import { approveReview, batchReview, submitReview } from "./database";

describe("submitReview", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("sends the stable idempotency key required by the review API", async () => {
    const response = { id: "existing-review", status: "awaiting_review" };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(response), { status: 200, headers: { "Content-Type": "application/json" } }),
    );

    await expect(submitReview("DELETE FROM users WHERE id = 1", "", "stable-key-1"))
      .resolves.toEqual(response);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/db_operations/submit-review",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "Idempotency-Key": "stable-key-1" }),
      }),
    );
  });

  it("sends reviewer_note and uses the backend batch endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({ status: "completed", total: 2, succeeded: 2, items: [] }), { status: 200 }),
    );
    await approveReview("review-1", "checked");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ reviewer_note: "checked" });

    await batchReview(["review-1", "review-2"], "approve", "batch checked");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/db_operations/reviews/batch");
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
      review_ids: ["review-1", "review-2"], action: "approve", reviewer_note: "batch checked",
    });
  });
});
