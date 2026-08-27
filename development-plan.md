# 数据库 RPA 机器人 — 7天开发计划

> **原始 7 天开发计划状态：✅ 已完成 (2026-07-06)**
> 此处“完成”仅表示原始 4 个开发阶段及 3 轮功能补充已经实现，
> 不表示后续安全、交付和商业化整改已经全部完成。
> 详细进度见 [PROJECT_STATUS.md](./PROJECT_STATUS.md)

## 总览

| 阶段 | 天数 | 主题 | 目标 | 状态 |
|---|---|---|---|---|
| Phase 1 | Day 1–2 | 基础设施搭建 | 数据库连接层、工具注册、SQL 生成链路跑通 | ✅ |
| Phase 2 | Day 3–4 | 安全与审核 | 备份机制、敏感操作拦截、审核 API | ✅ |
| Phase 3 | Day 5–6 | 前端交互 | SQL 编辑器、数据预览、审核弹窗、操作日志 | ✅ |
| Phase 4 | Day 7 | 集成测试与收尾 | 端到端测试、异常处理、文档 | ✅ |

---

## Phase 1: 基础设施搭建 (Day 1–2)

### 目标
打通"自然语言 → SQL → 执行 → 返回结果"最小闭环，只开放只读操作。

### Day 1 — 数据库连接器

**新建文件：**

| 文件 | 职责 |
|---|---|
| `backend/db_connector/__init__.py` | 模块入口 |
| `backend/db_connector/base.py` | 抽象基类：`connect()`, `execute()`, `query()`, `get_tables()`, `get_schema()`, `close()` |
| `backend/db_connector/mysql_impl.py` | MySQL 实现 (aiomysql/pymysql) |
| `backend/db_connector/sqlite_impl.py` | SQLite 实现 (aiosqlite) |
| `backend/db_connector/factory.py` | 工厂方法：根据配置创建连接器实例 |

**设计要点：**
- 复用现有抽象工厂模式，与 `llm/`、`embedding/` 保持一致风格
- 连接池管理，支持连接超时和重试
- `get_schema()` 返回表名、字段名、类型、注释，供 LLM 理解数据库结构

**配置扩展：**

| 文件 | 变更 |
|---|---|
| `backend/config.py` | 新增 `DB_TYPE`, `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DB_POOL_SIZE` 配置项 |

### Day 2 — Agent 工具注册 + SQL 生成链路

**新建文件：**

| 文件 | 职责 |
|---|---|
| `backend/agent/tools/database.py` | 注册 3 个数据库工具 |

**新增工具：**

| 工具名 | 功能 | 权限级别 |
|---|---|---|
| `get_db_schema` | 获取数据库表结构（表名、字段、索引） | 只读 |
| `query_db` | 执行 SELECT 查询，返回结果集（限制最大 500 行） | 只读 |
| `execute_sql` | 执行写操作（占位，此阶段仅记录不执行） | 需审核 |

**集成点：**

| 文件 | 变更 |
|---|---|
| `backend/agent/tools.py` | `ToolRegistry` 注册新增的 3 个数据库工具 |
| `backend/agent/context.py` | 系统提示词追加数据库相关规则和 safety 约束 |
| `backend/agent/classifier.py` | 意图分类新增 `database_query`、`database_modify` 意图 |

**验证标准：**
- 用户输入"查看 users 表有哪些字段"，Agent 返回表结构
- 用户输入"查询 orders 表中 status 为 pending 的记录"，Agent 执行 SELECT 并返回数据
- SELECT 语句在 500 行限制下安全运行

---

## Phase 2: 安全与审核 (Day 3–4)

### 目标
实现写操作的安全管控：备份 → 审核 → 执行 → 审计，敏感操作触发二次确认。

### Day 3 — 备份与回滚机制

**新建文件：**

| 文件 | 职责 |
|---|---|
| `backend/db_connector/backup.py` | `BackupManager` 类 |

**备份策略：**

```
写操作前 BackupManager 自动:
  1. 解析 SQL，识别受影响表
  2. 添加 WHERE 条件 (如果 DELETE/UPDATE 无 WHERE，拒绝执行)
  3. SELECT * FROM target_table WHERE <same_condition>
  4. 将结果集序列化为 INSERT 回滚语句，存入 backup 表
  5. 返回 backup_id 供后续回滚
```

**新建表：**

| 表名 | 字段 | 说明 |
|---|---|---|
| `db_operation_log` | id(UUID), operation_type, sql_text, affected_rows, backup_id, status, created_at | 审计日志 |
| `db_backups` | id(UUID), table_name, condition_sql, rollback_sql, data_snapshot(JSON), created_at | 备份快照 |

**安全规则引擎：**

| 文件 | 职责 |
|---|---|
| `backend/db_connector/safety.py` | `SafetyChecker` 类 |

**规则清单：**

| 规则 | 触发条件 | 动作 |
|---|---|---|
| 无 WHERE 写操作 | DELETE/UPDATE 不含 WHERE | 拒绝执行，提示必须指定条件 |
| DROP 操作 | DROP TABLE / DROP DATABASE | 强制二次确认弹窗 |
| TRUNCATE 操作 | TRUNCATE TABLE | 强制二次确认弹窗 |
| ALTER 操作 | ALTER TABLE | 强制二次确认弹窗 |
| 全表扫描 | SELECT 无 WHERE 且表 > 10万行 | 警告，建议加 LIMIT |
| 超大结果集 | SELECT 返回 > 5000 行 | 自动截断，提示精确条件 |

### Day 4 — 审核 API

**新建文件：**

| 文件 | 职责 |
|---|---|
| `backend/api/db_operations.py` | 数据库操作审核接口 |

**接口设计：**

| 方法 | 路径 | 功能 |
|---|---|---|
| `POST` | `/api/db/preview` | 提交 SQL 预览请求，返回受影响数据摘要（行数、表名、前10行样例） |
| `POST` | `/api/db/submit-review` | 提交 SQL 进入审核队列，返回审核任务 ID |
| `GET` | `/api/db/review/{id}` | 获取审核任务详情（SQL、预览数据、备份信息） |
| `POST` | `/api/db/review/{id}/approve` | 审核通过，执行 SQL |
| `POST` | `/api/db/review/{id}/reject` | 审核拒绝，丢弃请求 |
| `POST` | `/api/db/rollback/{backup_id}` | 回滚到指定备份点 |
| `GET` | `/api/db/logs` | 获取操作审计日志（分页，支持按时间/类型/表名筛选） |

**审核任务状态机：**

```
pending → previewing → awaiting_review → approved → executing → completed
                     ↘ rejected                  ↘ failed → (可重试)
```

**LLM SQL 生成增强：**

| 文件 | 变更 |
|---|---|
| `backend/agent/tools/database.py` | `execute_sql` 工具实际调用审核 API，返回审核链接 |
| `backend/agent/context.py` | 补充 SQL 生成规则：LIMIT 上限、WHERE 必填、字段白名单 |

**验证标准：**
- DELETE 无 WHERE → 拒绝并提示原因
- DROP TABLE → 弹出审核，状态机流转正常
- UPDATE 带 WHERE → 生成备份 → 审核通过 → 执行成功 → 日志记录

---

## Phase 3: 前端交互 (Day 5–6)

### 目标
提供直观的可视化界面，让用户预览数据、审核 SQL、查看操作历史。

### Day 5 — 数据库面板与数据预览

**新建文件：**

| 文件 | 职责 |
|---|---|
| `frontend/src/components/database/DatabasePanel.tsx` | 数据库操作主面板，Tab 切换（数据浏览 / SQL 查询 / 操作历史） |
| `frontend/src/components/database/DataTable.tsx` | 可排序、分页、筛选的数据预览表格 |
| `frontend/src/components/database/SchemaTree.tsx` | 左侧数据库结构树（库 → 表 → 字段），点击表名查看数据 |
| `frontend/src/api/database.ts` | 前端 API 层：数据库预览、审核提交、历史查询 |

**路由注册：**

| 文件 | 变更 |
|---|---|
| `frontend/src/App.tsx` | 新增 `/database` 路由 |
| `frontend/src/components/layout/Sidebar.tsx` | 侧边栏新增"数据库"导航项 |

**DatabasePanel 布局：**

```
┌──────────────────────────────────────────┐
│  [数据浏览]  [SQL 查询]  [操作历史]       │
├──────────┬───────────────────────────────┤
│ Schema   │                               │
│ Tree     │    数据预览表格                │
│          │    (排序/分页/筛选)            │
│ ├ users  │                               │
│ ├ orders │                               │
│ └ prods  │                               │
└──────────┴───────────────────────────────┘
```

### Day 6 — SQL 审核弹窗与操作日志

**新建文件：**

| 文件 | 职责 |
|---|---|
| `frontend/src/components/database/ReviewDialog.tsx` | SQL 审核确认弹窗（核心组件） |
| `frontend/src/components/database/SqlEditor.tsx` | SQL 输入框（语法高亮、快捷键 Ctrl+Enter 提交） |
| `frontend/src/components/database/OperationLog.tsx` | 操作历史列表（筛选、分页、详情展开） |
| `frontend/src/components/database/BackupBadge.tsx` | 备份状态标签（已备份/可回滚/已过期） |

**ReviewDialog 审核弹窗设计：**

```
┌─────────────────────────────────────────────┐
│  ⚠ 敏感操作确认                              │
├─────────────────────────────────────────────┤
│  操作类型: DELETE                            │
│  目标表:   orders                            │
│  影响行数: 1,234 条                          │
│  备份状态: ✅ 已生成回滚 SQL                  │
│                                             │
│  ┌─ SQL 预览 ────────────────────────────┐  │
│  │ DELETE FROM orders                    │  │
│  │ WHERE status = 'expired'              │  │
│  │ AND created_at < '2025-01-01'         │  │
│  └──────────────────────────────────────┘  │
│                                             │
│  ┌─ 影响数据预览 (前10条) ───────────────┐  │
│  │ id    │ status  │ created_at          │  │
│  │ 1001  │ expired │ 2024-11-15 09:30:00 │  │
│  │ 1002  │ expired │ 2024-10-01 12:00:00 │  │
│  │ ...   │ ...     │ ...                 │  │
│  └──────────────────────────────────────┘  │
│                                             │
│  操作理由: [________________] (必填)         │
│                                             │
│         [取消]          [确认执行]            │
└─────────────────────────────────────────────┘
```

**弹窗分级规则：**

| 操作类型 | 弹窗样式 | 确认方式 |
|---|---|---|
| INSERT | 蓝色提示 | 点击确认 |
| UPDATE | 黄色警告 | 点击确认 |
| DELETE | 橙色警告 | 点击确认 + 填写理由 |
| DROP/TRUNCATE/ALTER | 红色危险 | 点击确认 + 填写理由 + 输入表名二次验证 |

**验证标准：**
- 数据库面板可浏览表结构和数据
- SQL 编辑器提交后，预览数据正确展示
- DELETE 类操作弹出橙色确认弹窗，必须填写理由
- DROP 类操作弹出红色弹窗，必须输入目标表名才能确认
- 操作历史页可查看所有变更记录，含备份状态

---

## Phase 4: 集成测试与收尾 (Day 7)

### 目标
端到端测试覆盖完整链路，完善异常处理和用户引导。

### Day 7 上午 — 后端测试

**新建文件：**

| 文件 | 职责 |
|---|---|
| `backend/tests/test_db_connector.py` | 数据库连接器测试（连接、查询、Schema 获取） |
| `backend/tests/test_safety.py` | 安全规则测试（无 WHERE 拒绝、敏感操作拦截） |
| `backend/tests/test_backup.py` | 备份与回滚测试 |
| `backend/tests/test_db_api.py` | 审核 API 测试（状态机流转、审批通过/拒绝） |
| `backend/tests/test_e2e_rpa.py` | 端到端测试：自然语言 → SQL → 审核 → 执行 → 审计 |

**测试数据库：** 新建 `data/test_rpa.db`，预置 3 张测试表（users, orders, products），含异常数据场景。

### Day 7 下午 — 前端完善与文档

**完善项：**

| 文件 | 变更 |
|---|---|
| `frontend/src/components/chat/MessageBubble.tsx` | 识别数据库操作相关消息，内嵌 ReviewDialog 或结果表格 |
| `frontend/src/stores/databaseStore.ts` | 数据库状态管理（连接状态、审核队列、操作历史） |
| `frontend/src/components/database/` | 各组件 loading / error / empty 状态完善 |

**用户引导：**
- 首次进入数据库页面，展示引导提示：连接配置 → 浏览表结构 → 示例查询
- 审核弹窗首次出现时，展示安全说明

**回归验证：**
- 运行全部现有 12 个测试，确保新模块不影响已有功能
- 运行新增 5 个测试文件，覆盖 RPA 核心链路

**文档：**

| 文件 | 内容 |
|---|---|
| `E:/UV/SQL-RPA/usage-guide.md` | 用户使用指南（连接配置、查询示例、审核流程） |
| `E:/UV/SQL-RPA/api-reference.md` | API 接口文档（新增接口详细说明） |

---

## 每日交付物清单

| 天 | 新增文件 | 修改文件 | 测试文件 |
|---|---|---|---|
| Day 1 | `db_connector/` 5 文件 | `config.py` | — |
| Day 2 | `agent/tools/database.py` | `tools.py`, `context.py`, `classifier.py` | — |
| Day 3 | `backup.py`, `safety.py` | `models/schemas.py`, `models/database.py` | — |
| Day 4 | `api/db_operations.py` | `agent/tools/database.py`, `context.py` | — |
| Day 5 | `DatabasePanel.tsx`, `DataTable.tsx`, `SchemaTree.tsx`, `database.ts`(api) | `App.tsx`, `Sidebar.tsx` | — |
| Day 6 | `ReviewDialog.tsx`, `SqlEditor.tsx`, `OperationLog.tsx`, `BackupBadge.tsx` | — | — |
| Day 7 | 5 个测试文件, 2 个文档 | `MessageBubble.tsx`, 各组件状态完善 | 全部测试运行 |

---

## 风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| 数据库方言差异 (MySQL vs SQLite 语法不同) | SQL 执行失败 | 连接器层做方言适配，LLM prompt 注入目标库类型 |
| LLM 生成不安全的 SQL (注入风险) | 数据泄露 | 安全规则引擎拦截 + 参数化查询强制 |
| 大表备份耗时过长 | 用户等待超时 | 异步备份 + SSE 进度推送 + 采样备份选项 |
| 前端审核弹窗组件复杂度高 | 延期风险 | Phase 3 优先完成核心审核链路，编辑器美化降级处理 |
