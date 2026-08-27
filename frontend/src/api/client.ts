const BASE_URL = "";
import type { ApiErrorCode } from "./user-api-v1.generated";

const TOKEN_KEY = "sql_rpa_token";
const TENANT_KEY = "sql_rpa_tenant_id";
const ORGANIZATION_ID_KEY = "sql_rpa_organization_id";
const MEMBERSHIP_ID_KEY = "sql_rpa_membership_id";
const CONTEXT_TOKEN_KEY = "sql_rpa_organization_context";

const activeOrganizationControllers = new Set<AbortController>();
let organizationContextGeneration = 0;

interface OrganizationContextSnapshot {
  generation: number;
  organizationId: string | null;
  membershipId: string | null;
  contextToken: string | null;
}

function captureOrganizationContext(): OrganizationContextSnapshot {
  return {
    generation: organizationContextGeneration,
    organizationId: localStorage.getItem(ORGANIZATION_ID_KEY),
    membershipId: localStorage.getItem(MEMBERSHIP_ID_KEY),
    contextToken: localStorage.getItem(CONTEXT_TOKEN_KEY),
  };
}

function isSnapshotCurrent(snapshot: OrganizationContextSnapshot): boolean {
  return snapshot.generation === organizationContextGeneration
    && snapshot.organizationId === localStorage.getItem(ORGANIZATION_ID_KEY)
    && snapshot.membershipId === localStorage.getItem(MEMBERSHIP_ID_KEY)
    && snapshot.contextToken === localStorage.getItem(CONTEXT_TOKEN_KEY);
}

export function authHeaders(): Record<string, string> {
  const token = localStorage.getItem(TOKEN_KEY);
  const tenantId = localStorage.getItem(TENANT_KEY);
  const organizationId = localStorage.getItem(ORGANIZATION_ID_KEY);
  const membershipId = localStorage.getItem(MEMBERSHIP_ID_KEY);
  const contextToken = localStorage.getItem(CONTEXT_TOKEN_KEY);
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(tenantId ? { "X-Tenant-ID": tenantId } : {}),
    ...(organizationId ? { "X-Organization-ID": organizationId } : {}),
    ...(membershipId ? { "X-Membership-ID": membershipId } : {}),
    ...(contextToken ? { "X-Organization-Context": contextToken } : {}),
  };
}

export function abortOrganizationRequests(): void {
  organizationContextGeneration += 1;
  for (const controller of activeOrganizationControllers) controller.abort();
  activeOrganizationControllers.clear();
}

export interface TrackedRequest {
  controller: AbortController;
  promise: Promise<Response>;
  complete: () => void;
  isCurrentContext: () => boolean;
  assertCurrentContext: () => void;
}

export class StaleOrganizationContextError extends Error {
  constructor() {
    super("Organization context changed while the request was in progress");
    this.name = "StaleOrganizationContextError";
  }
}

export function isOrganizationRequestCancelled(error: unknown): boolean {
  return error instanceof StaleOrganizationContextError
    || (error instanceof DOMException && error.name === "AbortError")
    || (error instanceof Error && error.name === "AbortError");
}

function copyHeaders(source?: HeadersInit): Record<string, string> {
  if (!source) return {};
  if (source instanceof Headers) {
    const result: Record<string, string> = {};
    source.forEach((value, name) => { result[name] = value; });
    return result;
  }
  if (Array.isArray(source)) return Object.fromEntries(source);
  return { ...source };
}

export function trackedFetch(input: RequestInfo | URL, init: RequestInit = {}): TrackedRequest {
  const controller = new AbortController();
  const contextSnapshot = captureOrganizationContext();
  const headers = copyHeaders(init.headers);
  for (const [name, value] of Object.entries({ "X-Request-ID": createRequestId(), ...authHeaders() })) {
    if (!Object.keys(headers).some((existing) => existing.toLowerCase() === name.toLowerCase())) headers[name] = value;
  }
  activeOrganizationControllers.add(controller);
  let completed = false;
  const complete = () => {
    if (completed) return;
    completed = true;
    activeOrganizationControllers.delete(controller);
  };
  const isCurrentContext = () => isSnapshotCurrent(contextSnapshot);
  const assertCurrentContext = () => {
    if (!isCurrentContext()) throw new StaleOrganizationContextError();
  };
  controller.signal.addEventListener("abort", complete, { once: true });
  const promise = fetch(input, { ...init, headers, signal: controller.signal });
  return { controller, promise, complete, isCurrentContext, assertCurrentContext };
}

export class ApiError extends Error {
  status: number;
  detail: string;
  requestId?: string;
  code?: ApiErrorCode | string;
  fieldErrors?: Record<string, string[]>;
  constructor(
    status: number,
    detail: string,
    requestId?: string,
    code?: string,
    fieldErrors?: Record<string, string[]>,
  ) {
    super(detail);
    this.status = status;
    this.detail = detail;
    this.requestId = requestId;
    this.code = code;
    this.fieldErrors = fieldErrors;
  }
}

export function createRequestId(): string {
  return crypto.randomUUID();
}

export function formatApiError(error: unknown, fallback = "请求失败"): string {
  if (!(error instanceof ApiError)) return error instanceof Error ? error.message : fallback;
  const category = error.status === 401 ? "登录状态已失效"
    : error.status === 403 ? "没有权限执行此操作"
    : error.status === 409 ? "数据状态已发生变化"
    : error.status === 429 ? "请求过于频繁，请稍后重试"
    : error.status >= 500 ? "服务暂时不可用"
    : error.detail || fallback;
  const detail = error.detail && error.detail !== category ? `：${error.detail}` : "";
  const trace = error.requestId ? `（请求 ID：${error.requestId}）` : "";
  return `${category}${detail}${trace}`;
}

async function parseError(
  res: Response,
  method: string,
  path: string,
  assertCurrentContext: () => void,
): Promise<never> {
  let detail = `${method} ${path}: ${res.status}`;
  let code: string | undefined;
  let fieldErrors: Record<string, string[]> | undefined;
  let errorRequestId: string | undefined;
  try {
    const body = JSON.parse(await res.text());
    const errorBody = body.error && typeof body.error === "object" ? body.error : body;
    if (typeof errorBody.message === "string") detail = errorBody.message;
    else if (typeof body.detail === "string") detail = body.detail;
    else if (body.detail?.message) detail = body.detail.message;
    code = errorBody.code || body.code || body.error_code || body.detail?.code;
    errorRequestId = typeof errorBody.request_id === "string" ? errorBody.request_id : undefined;
    if (errorBody.field_errors && typeof errorBody.field_errors === "object") {
      fieldErrors = Object.fromEntries(Object.entries(errorBody.field_errors).map(([field, messages]) => [
        field,
        Array.isArray(messages) ? messages.map(String) : [String(messages)],
      ]));
    }
  } catch { /* keep default */ }
  assertCurrentContext();
  if (res.status === 401) {
    localStorage.removeItem(TOKEN_KEY);
    window.dispatchEvent(new Event("sql_rpa:unauthorized"));
  }
  throw new ApiError(
    res.status,
    detail,
    res.headers.get("X-Request-ID") || (typeof errorRequestId === "string" ? errorRequestId : undefined),
    code,
    fieldErrors,
  );
}

async function requestJson<T>(method: string, path: string, init?: RequestInit): Promise<T> {
  const request = trackedFetch(`${BASE_URL}${path}`, init);
  try {
    const res = await request.promise;
    if (!res.ok) await parseError(res, method, path, request.assertCurrentContext);
    if (res.status === 204) {
      request.assertCurrentContext();
      return undefined as T;
    }
    const body = await res.json() as T;
    request.assertCurrentContext();
    return body;
  } finally {
    request.complete();
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  return requestJson("GET", path);
}

export async function apiPost<T>(path: string, body?: unknown, extraHeaders: Record<string, string> = {}): Promise<T> {
  return requestJson("POST", path, {
    method: "POST",
    headers: {
      ...(body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...extraHeaders,
    },
    body: body instanceof FormData ? body : JSON.stringify(body),
  });
}

export async function apiDelete<T = void>(path: string): Promise<T> {
  return requestJson("DELETE", path, {
    method: "DELETE",
  });
}

export async function apiPut<T>(path: string, body?: unknown): Promise<T> {
  return requestJson("PUT", path, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
}

export async function apiPatch<T>(path: string, body?: unknown): Promise<T> {
  return requestJson("PATCH", path, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
}
