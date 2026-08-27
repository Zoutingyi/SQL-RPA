import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  StaleOrganizationContextError,
  abortOrganizationRequests,
  apiGet,
  apiPost,
  formatApiError,
} from "./client";

describe("API error contract", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("propagates request IDs and produces a permission-specific message", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(
      JSON.stringify({ detail: "Approver role required", error_code: "FORBIDDEN" }),
      { status: 403, headers: { "Content-Type": "application/json", "X-Request-ID": "rid-403" } },
    ));

    const error = await apiGet("/api/protected").catch((caught) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 403, requestId: "rid-403", code: "FORBIDDEN" });
    expect(formatApiError(error)).toBe("没有权限执行此操作：Approver role required（请求 ID：rid-403）");
  });

  it("parses the frozen nested error and field-error structure", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      error: {
        code: "VALIDATION_FAILED",
        message: "提交内容不合法",
        field_errors: { username: ["账号已存在"] },
        request_id: "body-request-id",
      },
    }), { status: 422, headers: { "Content-Type": "application/json", "X-Request-ID": "header-request-id" } }));

    const error = await apiPost("/api/auth/users", {}).catch((caught) => caught);
    expect(error).toMatchObject({
      status: 422,
      code: "VALIDATION_FAILED",
      detail: "提交内容不合法",
      requestId: "header-request-id",
      fieldErrors: { username: ["账号已存在"] },
    });
  });

  it("accepts a successful 204 response without attempting JSON parsing", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }));
    await expect(apiPost("/api/auth/change-password", {})).resolves.toBeUndefined();
  });

  it("adds a client request ID to every API request", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    );
    await apiGet("/api/health");
    expect(fetchMock).toHaveBeenCalledWith("/api/health", expect.objectContaining({
      headers: expect.objectContaining({ "X-Request-ID": expect.any(String) }),
    }));
  });

  it("adds the selected tenant context to every API request", async () => {
    localStorage.setItem("sql_rpa_tenant_id", "tenant-a");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    );
    await apiGet("/api/tenant-resource");
    expect(fetchMock).toHaveBeenCalledWith("/api/tenant-resource", expect.objectContaining({
      headers: expect.objectContaining({ "X-Tenant-ID": "tenant-a" }),
    }));
  });

  it("adds organization and membership context to every API request", async () => {
    localStorage.setItem("sql_rpa_organization_id", "department-a");
    localStorage.setItem("sql_rpa_membership_id", "membership-primary");
    localStorage.setItem("sql_rpa_organization_context", "signed-context");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    );

    await apiGet("/api/organization-resource");

    expect(fetchMock).toHaveBeenCalledWith("/api/organization-resource", expect.objectContaining({
      headers: expect.objectContaining({
        "X-Organization-ID": "department-a",
        "X-Membership-ID": "membership-primary",
        "X-Organization-Context": "signed-context",
      }),
    }));
  });

  it("aborts requests that belong to the previous organization context", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise(() => undefined));
    void apiGet("/api/slow-resource");
    const init = fetchMock.mock.calls[0][1];

    abortOrganizationRequests();

    expect(init?.signal?.aborted).toBe(true);
  });

  it("keeps a request tracked until its response body has been parsed", async () => {
    let resolveBody!: (value: { organization: string }) => void;
    const body = new Promise<{ organization: string }>((resolve) => { resolveBody = resolve; });
    const response = {
      ok: true,
      json: vi.fn(() => body),
    } as unknown as Response;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(response);

    const pending = apiGet<{ organization: string }>("/api/slow-body");
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const signal = fetchMock.mock.calls[0][1]?.signal;

    abortOrganizationRequests();
    resolveBody({ organization: "department-a" });

    expect(signal?.aborted).toBe(true);
    await expect(pending).rejects.toBeInstanceOf(StaleOrganizationContextError);
  });

  it("removes a request from tracking only after successful parsing", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ ok: true }),
    } as unknown as Response);

    await apiGet("/api/parsed");
    const signal = fetchMock.mock.calls[0][1]?.signal;
    abortOrganizationRequests();

    expect(signal?.aborted).toBe(false);
  });
});
