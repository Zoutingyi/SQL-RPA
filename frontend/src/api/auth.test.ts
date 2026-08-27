import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPatch: vi.fn(),
  apiPost: vi.fn(),
}));

vi.mock("./client", () => mocks);

import { assertV1AuthUser, changePassword, createUser, UserContractError } from "./auth";
import { USER_API_V1_SNAPSHOT } from "./user-api-v1.generated";

describe("user V1 API contract", () => {
  beforeEach(() => vi.clearAllMocks());

  it("uses the frozen OpenAPI and stable error-code snapshot", () => {
    expect(USER_API_V1_SNAPSHOT.contract_version).toBe("user-api-v1");
    expect(USER_API_V1_SNAPSHOT.error_codes["401"]).toBe("AUTH_REQUIRED");
    expect(USER_API_V1_SNAPSHOT.paths["/api/auth/me"].get).toBeDefined();
  });

  it("rejects incomplete identities instead of fabricating permissions", () => {
    expect(() => assertV1AuthUser({ id: "u1", username: "alice", role: "viewer" }))
      .toThrow(UserContractError);
  });

  it("preserves null membership role and permissions", () => {
    const user = assertV1AuthUser({
      id: "u1",
      username: "alice",
      display_name: null,
      phone: null,
      is_active: true,
      is_platform_admin: false,
      must_change_password: false,
      password_changed_at: null,
      current_membership: null,
      organization_memberships: [],
    });
    expect(user.current_membership).toBeNull();
    expect(user.organization_memberships).toEqual([]);
  });

  it("sends a caller-owned stable idempotency key", async () => {
    mocks.apiPost.mockResolvedValue({ id: "u1" });
    const input = {
      username: "alice", display_name: "Alice", organization_id: "department-a",
      job_title: "工程师", phone: "13800138000", role: null, password: null,
    };
    await createUser(input, "user-create-123");
    expect(mocks.apiPost).toHaveBeenCalledWith("/api/auth/users", input, {
      "Idempotency-Key": "user-create-123",
    });
  });

  it("uses the frozen password request field names", async () => {
    mocks.apiPost.mockResolvedValue(undefined);
    await changePassword({ current_password: "old-value", new_password: "new-value", confirm_password: "new-value" });
    expect(mocks.apiPost).toHaveBeenCalledWith("/api/auth/change-password", {
      current_password: "old-value", new_password: "new-value", confirm_password: "new-value",
    });
  });
});
