import { expect, test, type Page, type Route } from "@playwright/test";

type AccountKind = "admin" | "default_user" | "unassigned" | "recoverable" | "single" | "multi";

interface HarnessState {
  businessRequests: string[];
  createRequests: Array<{ body: Record<string, unknown>; idempotencyKey: string | undefined }>;
  revokedTokens: Set<string>;
}

const departmentA = { id: "department-a", membershipId: "membership-a", name: "研发部", primary: true };
const departmentB = { id: "department-b", membershipId: "membership-b", name: "项目部", primary: false };

function membership(department: typeof departmentA, role: string | null, userId: string) {
  return {
    id: department.membershipId,
    membership_id: department.membershipId,
    user_id: userId,
    organization_id: department.id,
    organization_name: department.name,
    organization_level: "department",
    organization_path: ["甲公司", department.name],
    company_id: "company-a",
    job_title: department.primary ? "工程师" : "兼职顾问",
    role,
    permissions: role ? ["document.read"] : null,
    is_primary: department.primary,
    active: true,
    version: 1,
  };
}

function context(item: ReturnType<typeof membership>) {
  return {
    company_id: item.company_id,
    organization_id: item.organization_id,
    membership_id: item.id,
    organization_level: item.organization_level,
    organization_path: item.organization_path,
    role: item.role,
    permissions: item.permissions,
    context_token: `context-${item.id}`,
    context_version: 1,
    is_primary: item.is_primary,
  };
}

function respond(route: Route, body: unknown, status = 200, headers?: Record<string, string>) {
  return route.fulfill({ status, headers, contentType: "application/json", body: status === 204 ? "" : JSON.stringify(body) });
}

function accountFor(username: string): AccountKind {
  if (username === "admin") return "admin";
  if (username === "default-user") return "default_user";
  if (username === "unassigned-user") return "unassigned";
  if (username === "recoverable-user") return "recoverable";
  if (username === "multi-user") return "multi";
  return "single";
}

async function installHarness(page: Page): Promise<HarnessState> {
  const state: HarnessState = { businessRequests: [], createRequests: [], revokedTokens: new Set() };
  let defaultPasswordChanged = false;
  const createdUsers: Array<Record<string, unknown>> = [];

  await page.addInitScript(() => localStorage.setItem("sql_rpa_onboarding_completed_v1", "true"));
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const authorization = request.headers().authorization || "";
    const token = authorization.replace("Bearer ", "");

    if (state.revokedTokens.has(token)) {
      return respond(route, { error: { code: "AUTH_REQUIRED", message: "Token revoked", field_errors: {}, request_id: "e2e-revoked" } }, 401);
    }

    if (path === "/api/auth/login" && request.method() === "POST") {
      const username = (request.postDataJSON() as { username: string }).username;
      const kind = accountFor(username);
      const userId = `${kind}-id`;
      const role = kind === "unassigned" || kind === "recoverable" || kind === "default_user" ? null : "admin";
      const memberships = kind === "admin" ? []
        : kind === "multi" || kind === "recoverable" ? [membership(departmentA, role, userId), membership(departmentB, "viewer", userId)]
        : [membership(departmentA, role, userId)];
      return respond(route, {
        access_token: `token-${kind}${defaultPasswordChanged ? "-new" : ""}`,
        token_type: "bearer",
        organization: memberships[0] ? context(memberships[0]) : null,
        organization_memberships: memberships,
        user: { id: userId, username, role, is_platform_admin: kind === "admin" },
      });
    }

    if (path === "/api/auth/me") {
      if (!token) {
        return respond(route, { error: { code: "AUTH_REQUIRED", message: "Unauthorized", field_errors: {}, request_id: "e2e-anonymous" } }, 401);
      }
      const kind = token.includes("default_user") ? "default_user"
        : token.includes("unassigned") ? "unassigned"
        : token.includes("recoverable") ? "recoverable"
        : token.includes("multi") ? "multi"
        : token.includes("single") ? "single" : "admin";
      const username = kind === "admin" ? "admin" : kind === "default_user" ? "default-user" : kind === "unassigned" ? "unassigned-user" : kind === "recoverable" ? "recoverable-user" : kind === "multi" ? "multi-user" : "single-user";
      const userId = `${kind}-id`;
      const role = kind === "unassigned" || kind === "recoverable" || kind === "default_user" ? null : "admin";
      const memberships = kind === "admin" ? []
        : kind === "multi" || kind === "recoverable" ? [membership(departmentA, role, userId), membership(departmentB, "viewer", userId)]
        : [membership(departmentA, role, userId)];
      const requestedMembershipId = request.headers()["x-membership-id"];
      const currentMembership = memberships.find((item) => item.id === requestedMembershipId) || memberships[0];
      return respond(route, {
        id: userId,
        username,
        display_name: kind === "admin" ? "默认管理员" : "测试用户",
        phone: "138****5678",
        role,
        auth_type: "jwt",
        is_active: true,
        is_platform_admin: kind === "admin",
        must_change_password: kind === "default_user" && !defaultPasswordChanged,
        password_changed_at: defaultPasswordChanged ? "2026-08-25T00:00:00Z" : null,
        organization_id: currentMembership?.organization_id || null,
        membership_id: currentMembership?.id || null,
        organization_level: currentMembership?.organization_level || null,
        current_membership: currentMembership || null,
        organization_memberships: memberships,
      });
    }

    if (path === "/api/auth/change-password" && request.method() === "POST") {
      defaultPasswordChanged = true;
      state.revokedTokens.add(token);
      return respond(route, null, 204);
    }

    if (path === "/api/auth/users" && request.method() === "POST") {
      if (!token.includes("admin")) {
        return respond(route, { error: { code: "PERMISSION_DENIED", message: "Platform user management permission required", field_errors: {}, request_id: "e2e-403" } }, 403);
      }
      const body = request.postDataJSON() as Record<string, unknown>;
      state.createRequests.push({ body, idempotencyKey: request.headers()["idempotency-key"] });
      const primary = membership(departmentA, body.role as string | null, "created-id");
      const user = {
        id: "created-id", username: body.username, display_name: body.display_name,
        phone: "138****8000", is_active: true, current_membership: primary,
      };
      createdUsers.push(user);
      return respond(route, { user, primary_membership: primary, used_default_password: body.password === null, must_change_password: body.password === null }, 201);
    }

    if (path === "/api/auth/users" && request.method() === "GET") {
      return respond(route, { items: createdUsers, page: 1, page_size: 20, total: createdUsers.length });
    }

    if (path === "/api/departments/tree") {
      return respond(route, { items: [
        { id: "company-a", name: "甲公司", level: "company", parent_id: null, company_id: "company-a", depth: 1, active: true, sort_order: 0 },
        { id: departmentA.id, name: departmentA.name, level: "department", parent_id: "company-a", company_id: "company-a", depth: 2, active: true, sort_order: 0 },
        { id: departmentB.id, name: departmentB.name, level: "department", parent_id: "company-a", company_id: "company-a", depth: 2, active: true, sort_order: 1 },
      ] });
    }
    if (path === "/api/departments/memberships/me") {
      const kind = token.includes("recoverable") ? "recoverable" : token.includes("multi") ? "multi" : token.includes("single") ? "single" : null;
      const userId = kind ? `${kind}-id` : "";
      const items = kind === "recoverable"
        ? [membership(departmentA, null, userId), membership(departmentB, "viewer", userId)]
        : kind === "multi"
          ? [membership(departmentA, "admin", userId), membership(departmentB, "viewer", userId)]
          : kind === "single" ? [membership(departmentA, "admin", userId)] : [];
      return respond(route, { items });
    }
    if (path === "/api/departments/context/switch" && request.method() === "POST") {
      const membershipId = (request.postDataJSON() as { membership_id: string }).membership_id;
      const userId = token.includes("recoverable") ? "recoverable-id" : "multi-id";
      const selected = membershipId === departmentB.membershipId
        ? membership(departmentB, "viewer", userId)
        : membership(departmentA, token.includes("recoverable") ? null : "admin", userId);
      return respond(route, context(selected));
    }
    if (path === "/api/notifications/unread-count") return respond(route, { count: 0 });
    if (path === "/api/telemetry") return respond(route, { accepted: true });
    if (path === "/api/db_operations/status") {
      return respond(route, { connected: true, db_type: "sqlite", db_name: "e2e", table_count: 1 });
    }
    if (["/api/conversations", "/api/documents", "/api/notifications"].includes(path)) {
      state.businessRequests.push(path);
      return respond(route, path === "/api/conversations" ? [] : { items: [] });
    }
    return respond(route, { items: [] });
  });

  return state;
}

async function login(page: Page, username: string) {
  await page.goto("/");
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill("E2ePassword-1234");
  await page.getByRole("button", { name: "登录" }).click();
}

test("默认管理员无部门登录且不发起部门业务请求", async ({ page }) => {
  const state = await installHarness(page);
  await login(page, "admin");
  await expect(page.getByText("平台管理模式")).toBeVisible();
  await expect(page.getByRole("link", { name: "用户管理" })).toBeVisible();
  expect(state.businessRequests).toEqual([]);
});

test("/me 契约不匹配时显示部署错误并保留令牌", async ({ page }) => {
  await installHarness(page);
  await page.route("**/api/auth/me", async (route) => {
    const authorization = route.request().headers().authorization;
    if (!authorization) {
      return respond(route, { error: { code: "AUTH_REQUIRED", message: "Unauthorized", field_errors: {}, request_id: "e2e-anonymous" } }, 401);
    }
    return respond(route, {
      id: "admin-id", username: "admin", display_name: "默认管理员",
      phone: null, is_active: true, is_platform_admin: true,
      must_change_password: false, password_changed_at: null,
      current_membership: null,
    });
  });
  await login(page, "admin");
  await expect(page.getByRole("heading", { name: "前后端部署版本不匹配" })).toBeVisible();
  await expect(page.getByText(/organization_memberships/)).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("sql_rpa_token"))).toBe("token-admin");
  await expect(page.getByRole("button", { name: "登录" })).toHaveCount(0);
});

test("管理员创建默认密码用户，空权限按 null 提交", async ({ page }) => {
  const state = await installHarness(page);
  await login(page, "admin");
  await page.getByRole("link", { name: "用户管理" }).click();
  await page.getByLabel("登录账号").fill("created-user");
  await page.getByLabel("用户姓名").fill("新建用户");
  await page.getByLabel("用户部门").selectOption("department-a");
  await page.getByLabel("职位").fill("工程师");
  await page.getByLabel("联系电话").fill("13800138000");
  await page.getByRole("button", { name: "创建用户" }).click();
  await expect(page.getByText(/已使用默认密码/)).toBeVisible();
  expect(state.createRequests).toHaveLength(1);
  expect(state.createRequests[0].body).toMatchObject({ password: null, role: null, job_title: "工程师", phone: "13800138000" });
  expect(state.createRequests[0].idempotencyKey).toMatch(/^user-create-/);
});

test("首次登录强制改密且改密后旧令牌失效", async ({ page }) => {
  const state = await installHarness(page);
  await login(page, "default-user");
  await expect(page.getByRole("heading", { name: "首次登录，请修改密码" })).toBeVisible();
  const oldToken = await page.evaluate(() => localStorage.getItem("sql_rpa_token"));
  await page.getByLabel("当前密码").fill("111111");
  await page.getByLabel("新密码", { exact: true }).fill("NewSecure-5678");
  await page.getByLabel("确认新密码").fill("NewSecure-5678");
  await page.getByRole("button", { name: "修改密码" }).click();
  await expect(page.getByRole("button", { name: "登录" })).toBeVisible();
  expect(state.revokedTokens.has(oldToken || "")).toBe(true);
  const status = await page.evaluate(async (token) => (await fetch("/api/auth/me", { headers: { Authorization: `Bearer ${token}` } })).status, oldToken);
  expect(status).toBe(401);
});

test("空权限用户只能访问个人设置，业务组件不发请求", async ({ page }) => {
  const state = await installHarness(page);
  await login(page, "unassigned-user");
  await expect(page.getByRole("heading", { name: "个人设置" })).toBeVisible();
  await expect(page.getByText(/尚未分配权限/)).toBeVisible();
  expect(state.businessRequests).toEqual([]);
});

test("空权限主职可主动切换到另一条已授权任职", async ({ page }) => {
  await installHarness(page);
  await login(page, "recoverable-user");
  await expect(page.getByRole("heading", { name: "个人设置" })).toBeVisible();
  await page.getByRole("combobox", { name: "选择其他有效部门任职" }).selectOption("membership-b");
  await page.getByRole("button", { name: "确认切换" }).click();
  await expect.poll(() => page.evaluate(() => localStorage.getItem("sql_rpa_membership_id"))).toBe("membership-b");
  await expect(page.getByLabel("当前部门")).toHaveValue("membership-b");
});

for (const scenario of [
  { username: "single-user", count: 1 },
  { username: "multi-user", count: 2 },
]) {
  test(`${scenario.count === 1 ? "单任职" : "多任职"}用户登录`, async ({ page }) => {
    await installHarness(page);
    await login(page, scenario.username);
    await expect(page.getByLabel("当前部门").locator("option")).toHaveCount(scenario.count);
  });
}

test("个人设置展示当前任职并可修改密码", async ({ page }) => {
  const state = await installHarness(page);
  await login(page, "single-user");
  await page.getByRole("link", { name: "个人设置" }).click();
  await expect(page.getByRole("region", { name: "账号资料" }).getByText("甲公司 / 研发部", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("138****5678")).toBeVisible();
  await page.getByLabel("当前密码").fill("Current-1234");
  await page.getByLabel("新密码", { exact: true }).fill("Another-5678");
  await page.getByLabel("确认新密码").fill("Another-5678");
  await page.getByRole("button", { name: "修改密码" }).click();
  await expect(page.getByRole("button", { name: "登录" })).toBeVisible();
  expect(state.revokedTokens.has("token-single")).toBe(true);
});

test("非管理员直接创建用户返回 403", async ({ page }) => {
  await installHarness(page);
  await login(page, "single-user");
  const result = await page.evaluate(async () => {
    const response = await fetch("/api/auth/users", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": "forbidden-create", Authorization: `Bearer ${localStorage.getItem("sql_rpa_token")}` },
      body: JSON.stringify({ username: "forbidden", display_name: "Forbidden", organization_id: "department-a", job_title: "工程师", phone: "13800138000", password: null, role: null }),
    });
    return { status: response.status, code: (await response.json()).error.code };
  });
  expect(result).toEqual({ status: 403, code: "PERMISSION_DENIED" });
});
