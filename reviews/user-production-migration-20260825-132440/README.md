# 用户体系正式数据库 0006 迁移记录

- 执行时间：2026-08-25 13:24（Asia/Shanghai）
- 目标数据库：`E:\UV\SQL-RPA\data\rag_agent.db`
- 迁移前 revision：`0004_organization_migration_audit`
- 迁移后 revision：`0006_user_identity_v1 (head)`
- 迁移路径：`0004 -> 0005 -> 0006`
- 迁移前备份：`rag_agent-before-0006.db`（已通过 `.gitignore` 排除）
- preflight：passed，issues=0
- postflight：passed，issues=0
- 用户数量：325，迁移前后相同
- `username_normalized` 空值：0
- 用户、组织任职与密码哈希守恒：全部通过

## 执行期间兼容处理

应用 `create_all` 曾在 Alembic revision 仍为 0004 时提前创建
`user_create_idempotency`。首次执行 0006 因表已存在而停止，revision 保持在 0005，
原 `users` 表和数据未被替换；失败过程留下的 `_alembic_tmp_users` 为空表（0 行）。

已完成以下安全处理：

1. 确认原 `users` 为 325 行、临时表为 0 行；
2. 删除仅由失败迁移产生的空临时表；
3. 修改 0006：若幂等表已存在，则严格核验列和唯一约束，兼容时复用，不兼容时阻断；
4. 重新执行 0006 并通过迁移后守恒检查。

校验报告见 `user-0006-pre.json` 和 `user-0006-post.json`。
