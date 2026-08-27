import { expect, test, type Page, type Route } from "@playwright/test";

type LoginMode = "platform_admin" | "single_membership" | "multi_membership";

interface OrganizationFixture {
  id: string;
  membershipId: string;
  name: string;
  role: string;
  primary: boolean;
}

interface ApiHarness {
  authenticatedMeHeaders: Array<Record<string, string>>;
  documentRequests: Array<{ membershipId: string; ordinal: number }>;
}

const company = { id: "company-a", name: "甲公司" };
const organizations: OrganizationFixture[] = [
  { id: "department-a", membershipId: "membership-a", name: "研发部", role: "admin", primary: true },
  { id: "department-b", membershipId: "membership-b", name: "项目部", role: "admin", primary: false },
];

test.beforeEach(async ({ page }) => {
  page.on("pageerror", (error) => process.stderr.write(`E2E_PAGE_ERROR ${error.stack || error.message}\n`));
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().includes("status of 401")) {
      process.stderr.write(`E2E_CONSOLE_ERROR ${message.text()}\n`);
    }
  });
});

function membership(organization: OrganizationFixture, userId = "user-a") {
  return {
    id: organization.membershipId,
    membership_id: organization.membershipId,
    user_id: userId,
    organization_id: organization.id,
    organization_level: "department",
    organization_name: organization.name,
    organization_path: [company.name, organization.name],
    company_id: company.id,
    role: organization.role,
    job_title: organization.primary ? "负责人" : "兼职负责人",
    is_primary: organization.primary,
    active: true,
    version: 1,
  };
}

function context(organization: OrganizationFixture) {
  return {
    company_id: company.id,
    organization_id: organization.id,
    membership_id: organization.membershipId,
    organization_level: "department",
    organization_path: [company.name, organization.name],
    role: organization.role,
    permissions: ["organization.manage"],
    context_token: `context-${organization.membershipId}`,
    context_version: 1,
    is_primary: organization.primary,
  };
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function installBrowserControls(page: Page): Promise<void> {
  await page.addInitScript(() => {
    localStorage.setItem("sql_rpa_onboarding_completed_v1", "true");

    const nativeFetch = window.fetch.bind(window);
    const controls = {
      slowHttpEnabled: false,
      slowHttpStarted: false,
      slowHttpController: null as ReadableStreamDefaultController<Uint8Array> | null,
      sseEnabled: false,
      sseStarted: false,
      sseController: null as ReadableStreamDefaultController<Uint8Array> | null,
      oldOrganizationWrites: 0,
      observer: null as MutationObserver | null,
    };
    Object.assign(window, { __organizationE2E: controls });

    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, location.origin);
      const headers = new Headers(init?.headers);
      const membershipId = headers.get("X-Membership-ID");

      if (controls.slowHttpEnabled && url.pathname === "/api/documents" && membershipId === "membership-a") {
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            controls.slowHttpController = controller;
            controls.slowHttpStarted = true;
          },
        });
        return new Response(stream, { status: 200, headers: { "Content-Type": "application/json" } });
      }

      if (controls.sseEnabled && url.pathname === "/api/chat") {
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            controls.sseController = controller;
            controls.sseStarted = true;
          },
        });
        return new Response(stream, {
          status: 200,
          headers: {
            "Content-Type": "text/event-stream",
            "X-Conversation-Id": "old-organization-conversation",
          },
        });
      }

      return nativeFetch(input, init);
    };
  });
}

async function installApiHarness(
  page: Page,
  mode: LoginMode,
  documentDelay?: (membershipId: string, ordinal: number) => number,
): Promise<ApiHarness> {
  const authenticatedMeHeaders: Array<Record<string, string>> = [];
  const documentRequests: Array<{ membershipId: string; ordinal: number }> = [];
  const activeOrganizations = mode === "multi_membership" ? organizations
    : mode === "single_membership" ? [organizations[0]]
    : [];
  const username = mode === "platform_admin" ? "platform-admin" : "alice";
  const userId = mode === "platform_admin" ? "platform-admin-id" : "user-a";

  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const headers = request.headers();

    if (path === "/api/auth/login" && request.method() === "POST") {
      const primary = activeOrganizations[0];
      return json(route, {
        access_token: `token-${mode}`,
        token_type: "bearer",
        tenant_id: primary?.id,
        tenants: [],
        organization: primary ? context(primary) : null,
        organization_memberships: activeOrganizations.map((item) => membership(item, userId)),
        user: {
          id: userId,
          username,
          role: primary?.role || "admin",
          is_platform_admin: mode === "platform_admin",
        },
      });
    }

    if (path === "/api/auth/me") {
      if (!headers.authorization) return json(route, { detail: "Unauthorized" }, 401);
      authenticatedMeHeaders.push(headers);
      const selected = organizations.find((item) => item.membershipId === headers["x-membership-id"]);
      return json(route, {
        id: userId,
        username,
        display_name: username,
        phone: null,
        role: selected?.role || "admin",
        is_active: true,
        is_platform_admin: mode === "platform_admin",
        must_change_password: false,
        password_changed_at: null,
        organization_id: selected?.id || null,
        membership_id: selected?.membershipId || null,
        organization_level: selected ? "department" : null,
        current_membership: selected ? membership(selected, userId) : null,
        organization_memberships: activeOrganizations.map((item) => membership(item, userId)),
      });
    }

    if (path === "/api/departments/tree") {
      return json(route, { items: activeOrganizations.length ? [
        { id: company.id, name: company.name, level: "company", parent_id: null, company_id: company.id, depth: 1, active: true, sort_order: 0 },
        ...activeOrganizations.map((item, index) => ({
          id: item.id,
          name: item.name,
          level: "department",
          parent_id: company.id,
          company_id: company.id,
          depth: 2,
          active: true,
          sort_order: index,
        })),
      ] : [] });
    }

    if (path === "/api/departments/memberships/me") {
      return json(route, { items: activeOrganizations.map((item) => membership(item, userId)) });
    }

    if (path === "/api/departments/context/switch" && request.method() === "POST") {
      const body = request.postDataJSON() as { membership_id: string };
      const selected = organizations.find((item) => item.membershipId === body.membership_id);
      if (!selected) return json(route, { detail: "Invalid organization context" }, 403);
      return json(route, context(selected));
    }

    if (path === "/api/documents") {
      const membershipId = headers["x-membership-id"] || "platform";
      const ordinal = documentRequests.length + 1;
      documentRequests.push({ membershipId, ordinal });
      const delay = documentDelay?.(membershipId, ordinal) || 0;
      if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
      const marker = `ORG_DATA::${membershipId}::${ordinal}`;
      try {
        return await json(route, {
          items: [{
            id: `${membershipId}-${ordinal}`,
            filename: marker,
            file_type: "txt",
            file_size: 10,
            chunk_count: 1,
            status: "ready",
            created_at: "2026-08-25T00:00:00Z",
          }],
          total: 1,
          page: 1,
          page_size: 20,
        });
      } catch {
        return;
      }
    }

    if (path === "/api/conversations") return json(route, []);
    if (path === "/api/notifications/unread-count") return json(route, { count: 0 });
    if (path === "/api/db_operations/status") {
      return json(route, { connected: true, db_type: "sqlite", db_name: "e2e", table_count: 1 });
    }
    if (path === "/api/telemetry") return json(route, { accepted: true });
    return json(route, { items: [] });
  });

  return { authenticatedMeHeaders, documentRequests };
}

async function login(page: Page, username: string): Promise<void> {
  await page.goto("/");
  expect(await page.evaluate(() => ({
    token: localStorage.getItem("sql_rpa_token"),
    organization: localStorage.getItem("sql_rpa_organization_id"),
    membership: localStorage.getItem("sql_rpa_membership_id"),
    context: localStorage.getItem("sql_rpa_organization_context"),
  }))).toEqual({ token: null, organization: null, membership: null, context: null });
  await expect(page.getByLabel("用户名")).toBeVisible();
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill("e2e-password");
  await page.getByRole("button", { name: "登录" }).click();
  await expect(page.locator(".sidebar-user-name")).toHaveText(username);
}

async function switchMembership(page: Page, membershipId: string): Promise<void> {
  const selector = page.getByLabel("当前部门");
  await expect(selector).toBeEnabled();
  await selector.selectOption(membershipId);
  await page.getByRole("button", { name: "确认切换" }).click();
  await expect.poll(() => page.evaluate(() => localStorage.getItem("sql_rpa_membership_id"))).toBe(membershipId);
}

async function startBackflowObserver(page: Page): Promise<void> {
  await page.evaluate(() => {
    const controls = (window as unknown as { __organizationE2E: {
      oldOrganizationWrites: number;
      observer: MutationObserver | null;
    } }).__organizationE2E;
    controls.oldOrganizationWrites = 0;
    controls.observer?.disconnect();
    controls.observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        for (const node of mutation.addedNodes) {
          const text = node.textContent || "";
          for (const match of text.matchAll(/ORG_DATA::(membership-[ab])::\d+/g)) {
            if (match[1] !== localStorage.getItem("sql_rpa_membership_id")) {
              controls.oldOrganizationWrites += 1;
            }
          }
          if (text.includes("OLD_SSE_ORGANIZATION_DATA")) controls.oldOrganizationWrites += 1;
          if (text.includes("OLD_HTTP_ORGANIZATION_DATA")) controls.oldOrganizationWrites += 1;
        }
      }
    });
    controls.observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  });
}

async function oldOrganizationWriteCount(page: Page): Promise<number> {
  return page.evaluate(() => (window as unknown as {
    __organizationE2E: { oldOrganizationWrites: number };
  }).__organizationE2E.oldOrganizationWrites);
}

test.describe("first login organization bootstrap", () => {
  for (const scenario of [
    { mode: "platform_admin" as const, username: "platform-admin", expectedMembership: null, expectedOptions: 0 },
    { mode: "single_membership" as const, username: "alice", expectedMembership: "membership-a", expectedOptions: 1 },
    { mode: "multi_membership" as const, username: "alice", expectedMembership: "membership-a", expectedOptions: 2 },
  ]) {
    test(`${scenario.mode} sends the complete first me context from clean storage`, async ({ page }, testInfo) => {
      await installBrowserControls(page);
      const harness = await installApiHarness(page, scenario.mode);

      await login(page, scenario.username);

      const firstMe = harness.authenticatedMeHeaders[0];
      expect(firstMe.authorization).toBe(`Bearer token-${scenario.mode}`);
      expect(firstMe["x-membership-id"] || null).toBe(scenario.expectedMembership);
      expect(firstMe["x-organization-id"] || null).toBe(
        scenario.expectedMembership ? "department-a" : null,
      );
      expect(await page.evaluate(() => localStorage.getItem("sql_rpa_membership_id"))).toBe(scenario.expectedMembership);
      await expect(page.getByLabel("当前部门").locator("option")).toHaveCount(scenario.expectedOptions);

      const evidence = {
        scenario: scenario.mode,
        first_me_membership: firstMe["x-membership-id"] || null,
        first_me_organization: firstMe["x-organization-id"] || null,
        clean_login_success: true,
      };
      await testInfo.attach(`${scenario.mode}-first-login.json`, {
        body: JSON.stringify(evidence, null, 2), contentType: "application/json",
      });
      process.stdout.write(`E2E_EVIDENCE ${JSON.stringify(evidence)}\n`);
    });
  }
});

test.describe("organization response backflow isolation", () => {
  test("slow HTTP body from the old membership never reaches the new DOM", async ({ page }, testInfo) => {
    await installBrowserControls(page);
    await installApiHarness(page, "multi_membership");
    await login(page, "alice");
    await startBackflowObserver(page);
    await page.evaluate(() => {
      (window as unknown as { __organizationE2E: { slowHttpEnabled: boolean } }).__organizationE2E.slowHttpEnabled = true;
    });

    await page.getByRole("link", { name: "文档库" }).click();
    await page.waitForFunction(() => (window as unknown as {
      __organizationE2E: { slowHttpStarted: boolean };
    }).__organizationE2E.slowHttpStarted);
    await switchMembership(page, "membership-b");
    await expect(page.getByText(/ORG_DATA::membership-b::/)).toBeVisible();

    await page.evaluate(() => {
      const controls = (window as unknown as { __organizationE2E: {
        slowHttpController: ReadableStreamDefaultController<Uint8Array> | null;
      } }).__organizationE2E;
      const body = JSON.stringify({
        items: [{
          id: "old-http",
          filename: "OLD_HTTP_ORGANIZATION_DATA",
          file_type: "txt",
          file_size: 1,
          chunk_count: 1,
          status: "ready",
          created_at: "2026-08-25T00:00:00Z",
        }], total: 1, page: 1, page_size: 20,
      });
      controls.slowHttpController?.enqueue(new TextEncoder().encode(body));
      controls.slowHttpController?.close();
    });
    await page.waitForTimeout(100);

    const writes = await oldOrganizationWriteCount(page);
    expect(writes).toBe(0);
    await expect(page.getByText("OLD_HTTP_ORGANIZATION_DATA")).toHaveCount(0);
    const evidence = { scenario: "slow_http", old_organization_dom_writes: writes };
    await testInfo.attach("slow-http-zero-backflow.json", {
      body: JSON.stringify(evidence, null, 2), contentType: "application/json",
    });
    process.stdout.write(`E2E_EVIDENCE ${JSON.stringify(evidence)}\n`);
  });

  test("late SSE chunks from the old membership are discarded after switching", async ({ page }, testInfo) => {
    await installBrowserControls(page);
    await installApiHarness(page, "multi_membership");
    await login(page, "alice");
    await startBackflowObserver(page);
    await page.evaluate(() => {
      (window as unknown as { __organizationE2E: { sseEnabled: boolean } }).__organizationE2E.sseEnabled = true;
    });

    await page.getByPlaceholder("输入消息，Enter 发送，Shift+Enter 换行").fill("启动慢 SSE");
    await page.getByRole("button", { name: "发送" }).click();
    await page.waitForFunction(() => (window as unknown as {
      __organizationE2E: { sseStarted: boolean };
    }).__organizationE2E.sseStarted);
    await switchMembership(page, "membership-b");

    await page.evaluate(() => {
      const controls = (window as unknown as { __organizationE2E: {
        sseController: ReadableStreamDefaultController<Uint8Array> | null;
      } }).__organizationE2E;
      const chunk = "event: answer_chunk\ndata: {\"delta\":\"OLD_SSE_ORGANIZATION_DATA\"}\n\n";
      controls.sseController?.enqueue(new TextEncoder().encode(chunk));
      controls.sseController?.close();
    });
    await page.waitForTimeout(100);

    const writes = await oldOrganizationWriteCount(page);
    expect(writes).toBe(0);
    await expect(page.getByText("OLD_SSE_ORGANIZATION_DATA")).toHaveCount(0);
    const evidence = { scenario: "slow_sse", old_organization_dom_writes: writes };
    await testInfo.attach("slow-sse-zero-backflow.json", {
      body: JSON.stringify(evidence, null, 2), contentType: "application/json",
    });
    process.stdout.write(`E2E_EVIDENCE ${JSON.stringify(evidence)}\n`);
  });

  test("100 rapid membership switches produce zero stale organization DOM writes", async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    await installBrowserControls(page);
    const harness = await installApiHarness(page, "multi_membership", (_membershipId, ordinal) => 180 + (ordinal % 5) * 45);
    await login(page, "alice");
    await startBackflowObserver(page);
    await page.getByRole("link", { name: "文档库" }).click();

    const transitions = 100;
    for (let index = 0; index < transitions; index += 1) {
      await switchMembership(page, index % 2 === 0 ? "membership-b" : "membership-a");
    }
    await page.waitForTimeout(700);

    const writes = await oldOrganizationWriteCount(page);
    const finalMembership = await page.evaluate(() => localStorage.getItem("sql_rpa_membership_id"));
    expect(finalMembership).toBe("membership-a");
    expect(writes).toBe(0);
    await expect(page.getByText(/ORG_DATA::membership-a::/)).toBeVisible();

    const evidence = {
      scenario: "high_frequency_switch",
      transitions,
      issued_document_requests: harness.documentRequests.length,
      old_organization_dom_writes: writes,
    };
    await testInfo.attach("high-frequency-zero-backflow.json", {
      body: JSON.stringify(evidence, null, 2), contentType: "application/json",
    });
    process.stdout.write(`E2E_EVIDENCE ${JSON.stringify(evidence)}\n`);
  });
});
