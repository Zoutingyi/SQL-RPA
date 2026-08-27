# 组织模块自动化测试与覆盖率报告

执行日期：2026-08-27

## 覆盖范围与门槛

核心文件全部纳入，未使用 omit/exclude：

- `backend/organization_context.py`
- `backend/organization_service.py`
- `backend/api/departments.py`

独立门槛：statement `>=90%`，branch `>=85%`。

## 执行结果

- 组织专项：`20 passed`。
- statement：`96.28%`（414/430行覆盖，pytest-cov statement口径为96.28%）。
- branch：`96.43%`（108/112分支覆盖）。
- 后端全量：`235 passed, 5 skipped, 1 warning`，JUnit见 `backend-full-junit.xml`。
- 5个skip均来自真实依赖测试模块，本地默认不开启 `RUN_REAL_INTEGRATIONS=1`。
- warning来自既有 PostgreSQL状态探测测试结束时的aiosqlite后台线程晚于事件循环关闭；测试本身通过，未造成失败，但应作为测试资源清理问题继续跟踪。

## 新增覆盖场景

- 组织API viewer权限拒绝、跨公司读取/写入/移动拒绝。
- 组织名称冲突并发创建、版本冲突、空名称及非法父节点。
- 同公司节点移动、后代路径更新、跨公司和非法层级移动。
- 有子节点/任职组织停用拒绝及空节点停用。
- 主职/兼职新增、更新、冲突、切换、替换、停用和过期拒绝。
- 伪造Membership、缺失Membership、上下文切换及旧路径/层级/有效期失效。
- 个人层owner边界、legacy作用域与当前写入归属。

## 真实容器测试失败项说明

已尝试后台启动Docker Desktop，但Linux engine命名管道未创建：

`failed to connect to docker API at npipe:////./pipe/dockerDesktopLinuxEngine`。

因此本机未能启动MySQL、PostgreSQL、Qdrant和Redis容器，也不能声称真实服务或多进程测试通过。
CI中的 `real-integrations` 作业已经固定对应服务镜像并执行这些测试；最终验收仍需取得该作业成功记录。
