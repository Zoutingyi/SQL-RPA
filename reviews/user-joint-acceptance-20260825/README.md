# 用户体系联合验收整改记录

日期：2026-08-25

## 已通过的本地验证

- 后端全量：`227 passed, 5 skipped`；跳过项均为真实容器集成测试。
- 前端单元测试：`48 passed`。
- Playwright：`15 passed`，包括 V1 用户管理、强制改密、无权限隔离和组织切换回流隔离。
- OpenAPI、TypeScript和错误码快照：`user-api-v1 contract snapshot is consistent`。
- 前端生产构建：通过。
- SQLite全新数据库：`upgrade head -> downgrade 0005 -> upgrade head` 通过。
- 旧用户迁移演练：用户数量、密码哈希守恒，电话在 0007 加密。

## CI强制门禁

稳定聚合作业名为 `user-acceptance-gate`。它只有在后端、前端 Playwright、
PostgreSQL/Redis真实集成及迁移往返全部成功时才成功。生产分支保护必须将该检查设为 required。

## 尚需外部结果

- 本机 Docker daemon 未启动，真实 PostgreSQL、Redis和多 Worker测试无法在本机执行；代码和服务已纳入 CI。
- 当前环境未提供 Bugbot/Security Review subagent 调用入口，需在支持 Review subagent 的环境重新执行并归档结果。
