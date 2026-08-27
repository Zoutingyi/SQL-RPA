# 租户整改——部门 E2E 验证报告

验证日期：2026-08-25  
执行环境：Windows、Node.js 24.15.0、Playwright Chromium 151  
测试文件：`frontend/e2e/organization-isolation.spec.ts`

## 一、验证结论

Playwright Chromium 共执行 6 个部门上下文场景，全部通过。

- 平台管理员首次登录：通过。
- 单任职用户首次登录：通过。
- 多任职用户首次登录：通过，首个上下文采用后端返回的主职。
- 旧组织慢 HTTP 响应体延迟回传：旧组织 DOM 回灌 `0` 次。
- 旧组织慢 SSE 分块延迟回传：旧组织 DOM 回灌 `0` 次。
- 主职/兼职连续切换 100 次：触发 402 个文档列表请求，旧组织 DOM 回灌 `0` 次。

因此，TB-BUG-02 和 TB-BUG-09 的前端完成标准已满足。

## 二、执行命令

```text
cd frontend
npm run test:e2e:organization
```

完整工程门禁：

```text
npm run build
npm run lint
npm run check:organization
npm run test
npm run test:e2e:organization
```

## 三、首次登录验证

每个场景使用独立 Chromium BrowserContext。进入登录页时以下认证及部门存储均为空：

- `sql_rpa_token`
- `sql_rpa_organization_id`
- `sql_rpa_membership_id`
- `sql_rpa_organization_context`

### 平台管理员

- 登录成功。
- 首个认证 `/api/auth/me` 携带访问令牌。
- `X-Organization-ID` 和 `X-Membership-ID` 均为空。
- 浏览器未继承历史部门上下文。

### 单任职用户

- 登录成功。
- 首个认证 `/api/auth/me` 携带 `department-a` 和 `membership-a`。
- 页面只展示一个有效任职选项。

### 多任职用户

- 登录成功。
- 首个认证 `/api/auth/me` 携带主职 `department-a` 和 `membership-a`。
- 页面展示主职和兼职两个有效任职选项。

## 四、零回灌验证方法

浏览器在 `document.body` 安装 MutationObserver，只统计带组织身份标记的数据节点。
新增节点中的 membership 与当前 localStorage membership 不一致时，计为一次旧组织回灌。

该统计直接观察最终用户可见 DOM，不以请求是否 abort 作为成功依据。即使底层请求无法取消，
只要旧结果写入页面，计数就会大于 0 并使测试失败。

### 慢 HTTP

1. 在 `membership-a` 下请求文档列表。
2. HTTP 响应头返回后，响应体保持未完成，并忽略 AbortSignal。
3. 切换到 `membership-b`，等待新组织文档展示。
4. 再交付旧组织响应体，其中包含 `OLD_HTTP_ORGANIZATION_DATA`。
5. 断言旧标记未进入 DOM，回灌计数为 `0`。

### 慢 SSE

1. 在 `membership-a` 下建立聊天 SSE 流，流保持打开并忽略 AbortSignal。
2. 切换到 `membership-b`。
3. 再向旧流发送包含 `OLD_SSE_ORGANIZATION_DATA` 的 answer chunk。
4. 断言旧分块未进入消息区，回灌计数为 `0`。

### 高频切换

1. 在文档页面持续发起带 membership 标记的慢请求。
2. 在主职 `membership-a` 与兼职 `membership-b` 之间连续切换 100 次。
3. 每次切换均走真实页面选择器、确认框、上下文切换 API、Store 清理和页面重挂载流程。
4. 共观察到 402 个文档请求，最终 membership 为 `membership-a`。
5. 全过程旧组织 DOM 回灌计数为 `0`。

## 五、证据输出

测试运行时输出如下结构化证据：

```text
E2E_EVIDENCE {"scenario":"platform_admin","first_me_membership":null,"first_me_organization":null,"clean_login_success":true}
E2E_EVIDENCE {"scenario":"single_membership","first_me_membership":"membership-a","first_me_organization":"department-a","clean_login_success":true}
E2E_EVIDENCE {"scenario":"multi_membership","first_me_membership":"membership-a","first_me_organization":"department-a","clean_login_success":true}
E2E_EVIDENCE {"scenario":"slow_http","old_organization_dom_writes":0}
E2E_EVIDENCE {"scenario":"slow_sse","old_organization_dom_writes":0}
E2E_EVIDENCE {"scenario":"high_frequency_switch","transitions":100,"issued_document_requests":402,"old_organization_dom_writes":0}
```

每个场景同时生成 JSON attachment；失败时保留截图、视频和 Playwright trace。

## 六、验证边界

本报告验证前端浏览器中的身份原子提交、请求取消、上下文代次校验、SSE 丢弃、Store 清理和
最终 DOM 隔离。API 响应由 Playwright 测试边界控制，以稳定制造不可取消的迟到响应。
后端组织鉴权、数据作用域和迁移正确性仍由后端集成测试及迁移演练负责。

## 七、2026-08-27 增量回归

- 增加无主职不自动选择兼职、显式兼职刷新保留、任职失效清理旧作用域的 Store 回归。
- 增加空权限任职切换到其他已授权任职的组件回归。
- 增加部门管理员/普通成员管理入口权限回归。
- 增加任职编辑、设主职、停用接口路径和并发版本回归。
- 前端 Vitest：`16` 个文件、`55 passed`。
- Playwright Chromium：部门套件 `6 passed`、用户套件 `10 passed`；原6个部门E2E全部继续通过，100次切换旧组织DOM回灌仍为`0`。
- lint、组织契约扫描及生产构建通过。
