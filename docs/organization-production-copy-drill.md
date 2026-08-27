# 四级组织生产副本迁移演练

仅在已脱敏、访问受控的生产数据库副本上执行。禁止直接以生产主库作为演练目标。

1. 恢复并记录只读副本标识，执行迁移前检查：
   `python backend/scripts/organization_migration_check.py --database-url <副本连接> --phase pre --source-label <副本标识> --output reviews/org-migration-pre.json --require-clean`
2. 对报告中的孤立租户、孤立任职逐项确认。异常保留在隔离报告中，禁止静默删除或自动归入默认组织。
3. 在副本执行 `alembic upgrade head`。
4. 执行迁移后守恒检查并持久化运行记录：
   `python backend/scripts/organization_migration_check.py --database-url <副本连接> --phase post --source-label <副本标识> --output reviews/org-migration-post.json --record --require-clean`
5. 验证租户映射数和旧租户数相等、组织任职数和旧任职数相等、所有业务表组织字段完整、每个用户每层恰有一个有效主职。
6. 运行四层授权参数化测试，再执行 `alembic downgrade 0003_legacy_tenant_organization_scope` 验证旧应用兼容读取；随后重新升级并重复第 4 步。
7. 将前后报告、校验和、迁移日志、回滚日志和审批记录一并归档。任何 `quarantined` 状态均阻止上线。
