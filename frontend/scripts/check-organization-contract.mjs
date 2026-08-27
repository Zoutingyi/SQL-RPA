import { readdirSync, readFileSync } from "node:fs";
import { extname, join, relative, resolve } from "node:path";

const root = resolve(import.meta.dirname, "../src");
const sourceExtensions = new Set([".ts", ".tsx"]);
const failures = [];

function walk(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? walk(path) : [path];
  });
}

for (const path of walk(root)) {
  if (!sourceExtensions.has(extname(path))) continue;
  const file = relative(root, path).replaceAll("\\", "/");
  const source = readFileSync(path, "utf8");

  if (file.startsWith("components/") && source.includes("租户")) {
    failures.push(`${file}: 普通用户组件不得显示“租户”，请使用“部门”`);
  }
  if (file !== "api/client.ts" && /\bfetch\s*\(/u.test(source)) {
    failures.push(`${file}: 必须通过 api/client.ts 的 trackedFetch 发起请求`);
  }
  if (/from\s+["'][^"']*tenantStore["']/u.test(source)) {
    failures.push(`${file}: tenantStore 已废弃，请使用 organizationStore`);
  }
  if (file === "api/departments.ts" && (/api\/tenants/u.test(source) || /from\s+["'][^"']*tenants["']/u.test(source))) {
    failures.push(`${file}: 部门 API 不得静默降级到旧 /api/tenants 契约`);
  }
}

if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}

console.log("Organization frontend contract scan passed.");
