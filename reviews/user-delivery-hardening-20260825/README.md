# 用户商业交付阻断补充整改记录

日期：2026-08-25

## 数据库

- 正式内部数据库已从 0006 升级至 `0008_user_phone_permission`。
- 0007 将 User.phone 的 4 个非空值全部迁移为 `ENC:v*` AES-GCM 密文。
- 电话 lookup hash 缺失：0。
- 迁移前数据库备份：`rag_agent-before-0007.db`（已从版本控制排除）。

## 自动化验证

- 用户交付加固专项：16 passed。
- 完整后端回归：227 passed，4 skipped，耗时 22.15 秒。
- SQLite 并发管理员互停：仅一个请求成功，最终保留一个有效平台管理员。
- OpenAPI 契约版本：`user-api-v1`。
- revision readiness 期望值：`0008_user_phone_permission`。

## 真实依赖验收

CI 已增加以下服务和门禁：

- 独立执行 SQLite 与 PostgreSQL `alembic upgrade head`；
- PostgreSQL advisory-lock 两管理员并发停用测试；
- Redis Lua 登录失败计数跨客户端共享测试。

本机 Docker daemon 未运行，真实 PostgreSQL/Redis 用例在本地显示为 skipped，必须以 CI
容器执行结果作为最终关闭证据，当前不得标记为已通过。
