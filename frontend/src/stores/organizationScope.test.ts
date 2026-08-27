import { describe, expect, it, vi } from "vitest";
import { getRegisteredOrganizationScopes, registerOrganizationReset, resetOrganizationScope } from "./organizationScope";

describe("organization scope reset registry", () => {
  it("resets every registered organization-scoped state exactly once", () => {
    const first = vi.fn();
    const second = vi.fn();
    const unregisterFirst = registerOrganizationReset("test-documents", first);
    const unregisterSecond = registerOrganizationReset("test-notifications", second);

    resetOrganizationScope();

    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);
    expect(getRegisteredOrganizationScopes()).toEqual(expect.arrayContaining(["test-documents", "test-notifications"]));
    unregisterFirst();
    unregisterSecond();
  });
});
