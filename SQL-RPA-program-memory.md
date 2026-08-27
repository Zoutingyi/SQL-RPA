# SQL-RPA 项目技术文档

> 数据库 RPA 机器人 — 自然语言驱动的数据库操作与安全审核系统
>
> 最后更新: 2026-07-05 | 版本 0.1.0

---

## 一、项目概述

SQL-RPA 是一个智能数据库操作机器人，集成 ReAct Agent 代理循环与自然语言 SQL 生成能力。用户通过聊天界面描述数据分析需求，Agent 自动生成 SQL 并执行，写操作必须经过四级安全审核弹窗确认后才生效，全程记录审计日志。

### 核心功能

| 功能 | 说明 |
|---|---|
| 自然语言 → SQL | 聊天输入自然语言，LLM 自动生成 SQL 并执行 |
| 数据浏览 | 可视化浏览数据库表结构、数据（排序/分页） |
| SQL 编辑器 | 手写 SQL，Ctrl+Enter 提交，自动检测读写类型 |
| 安全审核 | 写操作自动进入审核流程，四级弹窗分级确认 |
| 自动备份 | 写操作前自动生成回滚 SQL，7天内可回滚 |
| 操作审计 | 所有数据库变更记录到 `db_operation_log`，可筛选查询 |
| 安全引擎 | 17 条安全规则实时拦截危险 SQL（DROP/无WHERE写操作/堆叠注入等） |
| 记忆系统 | 长期用户画像记忆，含自动提取与去重 |

---

## 二、技术栈

### 后端

| 类别 | 技术 | 版本 |
|---|---|---|
| 语言 | Python | 3.14 |
| Web 框架 | FastAPI (async) | 0.139 |
| ASGI 服务器 | Uvicorn | 0.49 |
| ORM | SQLAlchemy (async) | 2.0.51 |
| 目标数据库 | SQLite (aiosqlite) / MySQL (aiomysql, stub) | — |
| 内部数据库 | SQLite + WAL 模式 + FTS5 全文索引 | — |
| LLM SDK | OpenAI (兼容协议) | 2.44 |
| 配置管理 | pydantic-settings | 2.14 |
| 数据验证 | Pydantic | 2.13 |
| 加密 | AES-256-GCM (cryptography) | 49.0 |
| SSE 流式 | sse-starlette | 3.4 |
| HTTP 客户端 | httpx | 0.28 |

### 前端

| 类别 | 技术 | 版本 |
|---|---|---|
| 语言 | TypeScript | 6.0 |
| 框架 | React | 19.2 |
| 构建 | Vite (Rolldown/Rust) | 8.1 |
| 路由 | react-router-dom | 7.18 |
| 状态管理 | Zustand | 5.0 |
| 样式 | Tailwind CSS | 4.3 |
| Markdown | react-markdown + remark-gfm | 10.1 |
| Linter | oxlint | 1.69 |

### 代码规模

| 层 | 文件数 | 代码行数 |
|---|---|---|
| 后端 Python | 49 | ~4,943 |
| 前端 TypeScript/TSX | 31 | ~2,236 |
| 测试文件 | 2 | ~900 |
| CSS | 1 | ~1,290 |
| **总计** | **83** | **~9,369** |

---

## 三、系统架构

```
┌─────────────────────────────────────────────────────────┐
│  前端 (Vite :5173)                                       │
│  ┌──────────┬──────────────┬──────────────┬───────────┐ │
│  │ ChatPanel│ DatabasePanel│ DocumentList │ Settings  │ │
│  │ (聊天)   │ (数据库面板)  │ (文档库)     │ (设置)    │ │
│  └──────────┴──────────────┴──────────────┴───────────┘ │
│  stores: chatStore / databaseStore / toastStore         │
│  api layer: chat / database / documents / settings      │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTP/SSE (Vite Proxy)
┌──────────────────────▼──────────────────────────────────┐
│  后端 (FastAPI :8000)                                    │
│  ┌──────────────────────────────────────────────────┐  │
│  │ Middleware: CORS → RateLimit → Auth → Logging     │  │
│  ├──────────────────────────────────────────────────┤  │
│  │ API Routers (6 个)                                │  │
│  │  /api/chat  /api/db_operations  /api/conversations│  │
│  │  /api/memories  /api/settings  /api/documents     │  │
│  ├──────────────────────────────────────────────────┤  │
│  │ Agent System (ReAct Loop)                         │  │
│  │  classifier → context → react_loop → tools (9个)  │  │
│  │  intercept → session_extract (记忆系统)           │  │
│  ├──────────────────────────────────────────────────┤  │
│  │ DB Connector → SafetyChecker → BackupManager      │  │
│  └──────────────────────────────────────────────────┘  │
│  内部 DB: rag_agent.db (SQLite)                         │
│  目标 DB: rpa.db (SQLite/MySQL)                         │
└─────────────────────────────────────────────────────────┘
```

### 数据存储

| 数据库 | 路径 | 用途 |
|---|---|---|
| RAG Agent DB | `data/rag_agent.db` | 会话、消息、记忆、画像、操作日志、备份快照、FTS5全文索引 |
| RPA Target DB | `data/rpa.db` | 用户目标数据库，Agent 在此执行查询和写操作 |

### RAG Agent DB 表结构

| 表名 | 说明 |
|---|---|
| `documents` | 文档元数据（file_hash唯一索引） |
| `conversations` | 会话记录 |
| `messages` | 聊天消息（含工具调用和来源引用） |
| `user_memories` | 用户长期记忆（含类型标记和废弃管理） |
| `user_profiles` | 用户画像（JSON聚合，版本号自增） |
| `db_operation_log` | 数据库操作审计日志 |
| `db_backups` | 写操作前数据备份快照 |
| `chunks_fts` | FTS5 全文搜索虚拟表 |

---

## 四、后端模块详情

### 4.1 配置层 — `config.py`

- **Settings 类**: 50+ 配置项，覆盖 LLM、Embedding、Qdrant、数据库、Agent、安全、记忆等
- **密钥管理**: `_init_settings()` 自动生成 `SECRET_KEY`，AES-256-GCM 解密 API Key
- **环境变量**: 通过 `.env` 文件或系统环境变量注入

### 4.2 中间件层 — `middleware/`

| 文件 | 类 | 功能 |
|---|---|---|
| `auth.py` | `SecurityMiddleware` | Bearer Token 认证（`secrets.compare_digest` 防时序攻击）+ 10MB请求体限制；空密钥时透传 |
| `ratelimit.py` | `RateLimitMiddleware` | 内存滑动窗口限速，默认 30 次/60s/IP，跳过 `/api/health` |
| `logging.py` | `RequestIDMiddleware` | 请求ID注入 + JSON格式访问日志 + 10MB轮转（保留5个历史文件） |

### 4.3 Agent 系统 — `agent/`

| 文件 | 类/函数 | 功能 |
|---|---|---|
| `classifier.py` | `classify_intent()` | 意图分类：规则优先（正则匹配危险意图 + 7类普通意图），LLM回退。返回 `IntentHint`（意图、置信度、工具白名单） |
| `context.py` | `ContextManager` | 构建系统提示词：注入数据库结构规则、安全策略、工具描述、用户画像。滑动窗口消息裁剪（80% token预算） |
| `react_loop.py` | `run_agent()` | ReAct主循环：意图分类→提示词构建→LLM流式调用→工具执行（含重试）→SSE事件产出。最大10轮/120秒超时 |
| `intercept.py` | `MemoryInterceptor` | 消息级记忆拦截：12条中英文正则→LLM JSON验证→写入MemoryStore（fire-and-forget） |
| `session_extract.py` | `SessionExtractor` | 会话级记忆提取：≥5条新用户消息触发→LLM批量提取→写入MemoryStore→重建画像 |

**意图类型**（7种）：

| 意图 | 触发条件 |
|---|---|
| `danger_bypass` | 用户试图绕过安全规则、提取系统提示词、全表删除 |
| `database_query` | 查询数据库表/字段/数据 |
| `database_modify` | INSERT/UPDATE/DELETE 请求 |
| `knowledge_retrieval` | 知识库相关提问（文档内容、概念解释） |
| `document_info` | 查询文档元信息（文件列表、大小、状态） |
| `personal_memory` | 个人身份/偏好/角色信息（触发记忆拦截） |
| `general_chat` | 普通对话、问候、无工具需求 |

**SSE 事件类型**（7种）：

| 事件 | 含义 |
|---|---|
| `thought` | Agent 思维链（意图分类+推理过程） |
| `answer_chunk` | 流式回答片段 |
| `tool_call` | 工具调用（含工具名和参数） |
| `tool_result` | 工具执行结果 |
| `sources` | 文档引用来源 |
| `done` | Agent 循环完成 |
| `error` | 执行错误（已脱敏） |

### 4.4 Agent 工具链 — `agent/tools/`

| 工具名 | 实现文件 | 功能 | 权限 |
|---|---|---|---|
| `get_db_schema` | `database.py` | 列出所有表 / 获取指定表结构 | 只读 |
| `query_db` | `database.py` | 执行只读 SELECT（强制LIMIT 500，多语句拦截，写操作关键字拦截） | 只读 |
| `execute_sql` | `database.py` | 写操作→SafetyChecker检查→提交审核队列→返回review_id | 需审核 |
| `search_docs` | `__init__.py` | 混合语义检索知识库（stub） | 只读 |
| `calculator` | `__init__.py` | 安全数学表达式求值（AST白名单，仅+-*/） | 只读 |
| `list_documents` | `__init__.py` | 列出已上传文档（stub） | 只读 |
| `get_document_info` | `__init__.py` | 获取指定文档详情（stub） | 只读 |
| `web_search` | `__init__.py` | 网络搜索（stub） | 只读 |
| `recall_memory` | `__init__.py` | 用户记忆语义搜索（调用MemoryStore） | 只读 |

**ToolRegistry**: 统一注册、执行、重试管理。工具执行异常自动脱敏（隐藏traceback和内部路径）。

### 4.5 数据库连接器 — `db_connector/`

| 文件 | 类 | 功能 |
|---|---|---|
| `base.py` | `DatabaseConnector` (ABC) | 抽象基类：`connect()`, `close()`, `health_check()`, `get_tables()`, `get_schema()`, `query()`, `execute()` |
| `sqlite_impl.py` | `SqliteConnector` | SQLite实现：aiosqlite + WAL模式 + PRAGMA table_info 结构查询 + 参数化查询 |
| `mysql_impl.py` | `MySQLConnector` | MySQL实现（stub，aiomysql预留，标记为Phase 1 stub） |
| `factory.py` | `create_connector()` | 单例工厂：根据 `db_type` 配置创建连接器实例，自动连接 |
| `safety.py` | `SafetyChecker` | **17条安全规则**：SQL分类（read/write/dangerous_ddl）、多语句检测、无WHERE写操作拦截、WHERE 1=1检测、全表扫描告警、结果集过大告警 |
| `backup.py` | `BackupManager` | 写前快照→INSERT回滚SQL→回滚执行→验证→7天自动过期 |

**SafetyChecker 规则清单**（17条）：

| # | 规则 | 动作 |
|---|---|---|
| 1 | 多语句（含 `;`） | 直接拒绝 |
| 2 | DROP TABLE / DATABASE | 直接拒绝 |
| 3 | TRUNCATE TABLE | 直接拒绝 |
| 4 | ALTER TABLE | 直接拒绝 |
| 5 | CREATE TABLE / INDEX | 直接拒绝 |
| 6 | GRANT / REVOKE | 直接拒绝 |
| 7 | DELETE 无 WHERE | 直接拒绝 |
| 8 | UPDATE 无 WHERE | 直接拒绝 |
| 9 | DELETE WHERE 1=1 （等价值检测） | 直接拒绝 |
| 10 | UPDATE WHERE 1=1 | 直接拒绝 |
| 11 | INSERT INTO（无WHERE） | 允许，标记需要审核 |
| 12 | SELECT 全表扫描（>10万行表） | 警告 |
| 13 | SELECT 结果 > 5000 行 | 警告，建议加LIMIT |
| 14 | SELECT（正常） | 允许 |
| 15 | DELETE 含 WHERE（非1=1） | 允许，标记需要审核 |
| 16 | UPDATE 含 WHERE（非1=1） | 允许，标记需要审核 |
| 17 | 列名注入（排序参数白名单校验） | 400拒绝 |

### 4.6 API 层 — `api/`

| 路由模块 | 前缀 | 端点 | 说明 |
|---|---|---|---|
| `chat.py` | `/api` | `POST /api/chat` | SSE流式聊天：接收消息→保存→ReAct循环→SSE事件流 |
| `conversations.py` | `/api/conversations` | 5个端点 | 会话CRUD：列表/创建/删除/重命名/消息列表 |
| `db_operations.py` | `/api/db_operations` | **12个端点** | 数据库操作核心API |
| `documents.py` | `/api/documents` | 3个端点(stub) | 文档列表/上传/删除 |
| `settings.py` | `/api/settings` | 3个端点 | 配置读取/保存/连接测试 |
| `memories.py` | `/api/memories` | 6个端点 | 记忆CRUD/画像生成 |

**db_operations 端点详情**（12个）：

| 方法 | 路径 | 功能 |
|---|---|---|
| `GET` | `/api/db_operations/status` | 数据库连接状态 |
| `GET` | `/api/db_operations/tables` | 列出所有表（含列信息和行数） |
| `GET` | `/api/db_operations/tables/{name}` | 获取单表结构 |
| `GET` | `/api/db_operations/tables/{name}/data` | 分页+排序数据查询（列名白名单校验） |
| `POST` | `/api/db_operations/preview` | SQL安全分类+数据预览（SELECT返回前10行，写操作预估影响行数） |
| `POST` | `/api/db_operations/submit-review` | 提交写操作到审核队列 |
| `GET` | `/api/db_operations/review/{id}` | 获取审核任务详情 |
| `POST` | `/api/db_operations/review/{id}/approve` | 审核通过：备份→执行→审计 |
| `POST` | `/api/db_operations/review/{id}/reject` | 审核拒绝：丢弃请求 |
| `POST` | `/api/db_operations/rollback/{id}` | 回滚到指定备份点 |
| `GET` | `/api/db_operations/logs` | 操作审计日志（分页/类型/表名/状态筛选） |

**审核状态机**：
```
pending → submitting → awaiting_review → approved → executing → completed
                       ↘ rejected        ↘ failed → (可重试)
```

### 4.7 记忆系统 — `memory/`

| 文件 | 类 | 功能 |
|---|---|---|
| `store.py` | `MemoryStore` | SQLite文本搜索（ILIKE多词OR匹配）+ CRUD + 超限自动淘汰（默认100条，超出时软删除最旧记录） |
| `profile.py` | `ProfileManager` | 聚合非废弃记忆→按type分组→Jaccard相似度去重（阈值0.7）→保存UserProfile→格式化为LLM提示词文本 |

**记忆类型**（6种）：

| memory_type | 含义 | 提取方式 |
|---|---|---|
| `identity` | 身份信息（姓名、称呼） | 正则 + LLM 确认 |
| `role` | 职业/角色 | 正则 + LLM 确认 |
| `preference` | 偏好/喜好 | 正则 + LLM 确认 |
| `decision` | 用户做出的决定 | 正则 + LLM 确认 |
| `skill` | 技能 | LLM 提取 |
| `fact` | 其他一般事实 | LLM 提取（默认） |

### 4.8 LLM 层 — `llm/`

| 文件 | 类/函数 | 功能 |
|---|---|---|
| `base.py` | `BaseLLM` (ABC), `ToolCall`, `ChatMessage`, `LLMResponse` | 抽象基类 + 数据类型定义 |
| `openai_impl.py` | `OpenAILLM` | OpenAI兼容实现：同步`chat()`和非流式`chat_stream()`，工具调用块自动缓冲 |
| `factory.py` | `create_llm()` | 工厂方法：根据 `llm_provider` 创建实例 |

**支持的 LLM 供应商**：OpenAI / DeepSeek / 智谱 / 月之暗面 / 通义千问 / Groq / Ollama / 自定义（通过OpenAI兼容协议）

### 4.9 抽象层（预留）

| 目录 | 基类 | 状态 |
|---|---|---|
| `embedding/` | `BaseEmbedding` | factory stub（`NotImplementedError`） |
| `vectordb/` | `BaseVectorDB` | factory stub |
| `textdb/` | `BaseTextDB` | 仅抽象基类，无实现 |
| `reranker/` | `BaseReranker` | factory stub |
| `ocr/` | `BaseOCR` | factory stub |
| `rag/` | — | 仅 `__init__.py`，无实现 |
| `storage/` | — | 仅 `__init__.py`，无实现 |
| `worker/` | — | 仅 `__init__.py`，无实现 |

### 4.10 基础层

| 目录 | 类/函数 | 功能 |
|---|---|---|
| `models/database.py` | `init_db()`, `async_session` | SQLAlchemy async引擎+会话+表创建+FTS5初始化+自动迁移 |
| `models/schemas.py` | 7个ORM模型 + 3个枚举 | 数据模型定义 |
| `utils/crypto.py` | `encrypt()`, `decrypt()` | AES-256-GCM加解密，`ENC:<base64>`格式 |

---

## 五、前端模块详情

### 5.1 路由结构

| 路径 | 组件 | 功能 |
|---|---|---|
| `/` | `ChatPanel` | 主聊天界面（Agent对话） |
| `/documents` | `DocumentList` | 文档管理（占位） |
| `/database` | `DatabasePanel` | **数据库面板**（3 Tab） |
| `/settings` | `SettingsPage` | 系统设置（占位） |
| `/memories` | `MemoryList` | 记忆管理（占位） |

### 5.2 数据库面板 — `components/database/`

| 组件 | 功能 | 关键特性 |
|---|---|---|
| `DatabasePanel.tsx` | 主面板容器 | 3Tab切换（数据浏览/SQL查询/操作历史），集成6个子组件 |
| `SchemaTree.tsx` | 左侧表树 | 表列表→点击选中→展开列信息，行数展示 |
| `DataTable.tsx` | 数据表格 | 排序（表头点击↑↓）、分页、骨架屏加载、NULL值渲染 |
| `SqlEditor.tsx` | SQL编辑器 | 等宽深色背景、读写自动检测（颜色条区分）、Ctrl+Enter提交、执行/预览双按钮 |
| `ReviewDialog.tsx` | 四级审核弹窗 | 蓝(INSERT)/黄(UPDATE)/橙(DELETE)/红(DROP)四级、操作理由必填、表名二次验证、数据预览、Portal渲染 |
| `OperationLog.tsx` | 操作日志 | 类型/状态筛选、分页、点击展开完整SQL、回滚按钮 |
| `BackupBadge.tsx` | 备份标签 | 已备份/已回滚/已过期状态 + 回滚操作 |

**ReviewDialog 四级规则**：

| 操作类型 | 颜色 | 确认方式 |
|---|---|---|
| INSERT | 蓝色 | 点击确认 |
| UPDATE | 黄色 | 点击确认 |
| DELETE | 橙色 | 确认 + 必填操作理由 |
| DROP/TRUNCATE/ALTER | 红色 | 确认 + 必填理由 + 输入表名二次验证 |

### 5.3 状态管理 — `stores/`

| Store | 管理状态 | 关键操作 |
|---|---|---|
| `chatStore.ts` | 消息列表、会话列表、SSE状态、当前会话ID | `send()` 流式发送、`stop()` 中止、会话CRUD |
| `databaseStore.ts` | 表列表、选中表、列结构、操作日志 | `loadTables()`, `selectTable()`, `loadOperations()` |
| `toastStore.ts` | Toast通知队列 | `addToast()` 3秒/5秒自动关闭 |

### 5.4 API 层 — `api/`

| 文件 | 导出函数 | 对接后端 |
|---|---|---|
| `client.ts` | `apiGet<T>()`, `apiPost<T>()`, `apiDelete<T>()`, `apiPut<T>()` | 通用HTTP客户端，自动注入Bearer token |
| `chat.ts` | `sendMessage()` | SSE流式 `/api/chat` |
| `conversations.ts` | 5个CRUD函数 | `/api/conversations/*` |
| `database.ts` | 11个函数 | `/api/db_operations/*` 全端点覆盖 |
| `documents.ts` | 3个CRUD函数 | `/api/documents/*` |
| `memories.ts` | `listMemories()` | `/api/memories` |
| `settings.ts` | 3个函数 | `/api/settings/*` |

### 5.5 共享组件

| 组件 | 功能 |
|---|---|
| `Icons.tsx` | 25个SVG图标（Chat/Doc/Settings/Database/Table/SQL/Trash/Copy/Search等） |
| `ConfirmDialog.tsx` | Context式确认弹窗（Portal渲染，支持danger/default变体） |
| `Toast.tsx` | Portal通知提示（success/error/info，自动关闭动画） |
| `MainLayout.tsx` | 主布局（左侧边栏 + 右侧内容区） |
| `Sidebar.tsx` | 侧边栏导航 + 会话列表（新建/删除/重命名） |

---

## 六、安全设计

### 6.1 多层防护架构

```
用户输入 → 前端审核弹窗 → API认证 → 速率限制
  → Agent意图分类（危险意图拦截）
  → 系统提示词注入防御
  → query_db 工具层 SQL 只读校验 + 多语句拦截
  → SafetyChecker 17条规则
  → BackupManager 写前备份
  → 操作审计日志
  → 错误脱敏（隐藏traceback和内部路径）
```

### 6.2 安全特性清单

| 类别 | 措施 |
|---|---|
| 认证 | Bearer Token（`secrets.compare_digest` 防时序攻击），空密钥时开发模式透传 |
| 速率限制 | 滑动窗口 30次/60s/IP（可配置），Redis升级路径预留 |
| CORS | 仅允许 `localhost:5173`，方法/头部白名单 |
| 请求体 | 10MB上限，文件上传路径豁免 |
| SQL注入 | 多语句拦截 + 写操作关键字全词匹配 + 排序字段列名白名单 + 表名正则校验 |
| 提示注入 | 分类器优先检测危险意图，系统提示词含防注入规则，LLM注入经工具层硬拦截兜底 |
| 数据保护 | AES-256-GCM加密存储API密钥，敏感字段（password/token等）不展示原始内容 |
| 错误脱敏 | 工具异常→中文通用消息，全局异常处理器→500，SSE error→无traceback/路径/密码 |
| 操作审计 | 所有写操作记录到 `db_operation_log`（SQL/影响行数/操作人/状态/时间） |
| 日志安全 | 日志轮转（10MB/5文件），JSON格式，不含敏感信息 |
| 备份回滚 | 写前自动快照→INSERT回滚SQL→7天有效期 |

---

## 七、测试覆盖

### 7.1 测试文件

| 文件 | 行数 | 覆盖领域 |
|---|---|---|
| `test_all_features.py` | 499 | 14大类：健康检查、会话管理、Agent聊天、数据库操作API（17项）、数据库连接器、设置、记忆系统、文档管理、模块完整性（32模块）、SafetyChecker（16规则）、安全防护（10项）、记忆系统单元测试、前端编译（TypeScript+Vite）、错误脱敏 |
| `test_security.py` | 499 | 9大安全隐患专项验证：认证、SQL注入、提示注入、密钥管理、速率限制、错误脱敏、CORS、请求体限制、日志轮转 |

### 7.2 测试结果（2026-07-05）

```
76 项全部通过，0 失败
- TypeScript 编译: 零错误
- Vite 构建: 成功 (385KB JS + 50KB CSS)
- Python 编译: 零错误
- 32 个后端模块: 全部导入成功
- 17 条安全规则: 全部正确拦截
- 12 个 API 端点: 全部正常响应
```

---

## 八、项目进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 1 | 数据库连接器、Agent工具、LLM/ReAct循环、聊天API | ✅ 完成 |
| Phase 2 | 安全引擎(17规则)、备份回滚、审核API(12端点)、记忆系统 | ✅ 完成 |
| Phase 3 | 前端数据库面板：SchemaTree/DataTable/SqlEditor/ReviewDialog/OperationLog/BackupBadge | ✅ 完成 |
| Phase 4 | 集成测试、用户引导、文档、异常处理完善 | ⬜ 待开始 |

---

## 九、启动方式

### 后端

```bash
cd E:\UV\SQL-RPA\backend
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

### 前端

```bash
cd E:\UV\SQL-RPA\frontend
npm run dev           # 开发模式 (Vite :5173 → API代理到 :8000)
npm run build         # 生产构建
```

### 运行测试

```bash
# 先启动后端，再运行：
python test_all_features.py
python test_security.py
```

---

## 十、配置项摘要

### `.env` 关键配置

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SECRET_KEY` | 自动生成 | AES-256-GCM 加密密钥 |
| `LLM_PROVIDER` | `openai` | LLM供应商 |
| `LLM_MODEL` | `gpt-4o` | LLM模型名 |
| `LLM_API_KEY` | — | LLM API密钥（支持加密格式） |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | LLM API地址 |
| `DB_TYPE` | `sqlite` | 目标数据库类型 |
| `DB_SQLITE_PATH` | `./data/rpa.db` | SQLite目标库路径 |
| `API_KEY` | — | 前端API认证密钥（空=开发模式） |
| `MAX_LOOP_ITERATIONS` | `10` | Agent最大循环轮数 |
| `MAX_TOTAL_TIME` | `120` | Agent最大执行时间(秒) |
| `MEMORY_MAX_COUNT` | `100` | 最大记忆条数 |

---

## 十一、目录结构

```
SQL-RPA/
├── .env                          # 环境变量
├── pyproject.toml                # Python 项目配置
├── main.py                       # (根目录遗留文件)
├── SQL-RPA-program-memory.md     # 本文档
├── development-plan.md           # 原始开发计划
├── project-architecture.md       # 架构文档
├── phase1-4-detailed-plan.md     # 各阶段详细计划(4个文件)
├── plan1问题及整改.md             # 安全审计报告
├── test_all_features.py          # 全功能测试(499行)
├── test_security.py             # 安全专项测试(499行)
│
├── backend/                      # 后端 (49个.py文件, ~4943行)
│   ├── main.py                   # FastAPI入口
│   ├── config.py                 # 配置管理
│   ├── agent/                    # ReAct Agent系统 (6文件)
│   │   └── tools/                # 工具注册 + 数据库工具 (2文件)
│   ├── api/                      # REST + SSE接口 (6文件)
│   ├── db_connector/             # 数据库连接器 + 安全引擎 (7文件)
│   ├── llm/                      # LLM抽象层 (3文件)
│   ├── memory/                   # 用户记忆系统 (2文件)
│   ├── middleware/               # 中间件 (3文件)
│   ├── models/                   # 数据模型 (2文件)
│   ├── embedding/                # 嵌入层(预留) (3文件)
│   ├── vectordb/                 # 向量数据库(预留) (3文件)
│   ├── textdb/                   # 全文检索(预留) (1文件)
│   ├── reranker/                 # 重排序(预留) (3文件)
│   ├── ocr/                      # OCR(预留) (3文件)
│   ├── rag/                      # RAG管道(预留) (1文件)
│   ├── storage/                  # 存储(预留) (1文件)
│   ├── utils/                    # 加密工具 (1文件)
│   └── worker/                   # 后台任务(预留) (1文件)
│
├── frontend/                     # 前端 (31个.ts/.tsx文件, ~2236行)
│   └── src/
│       ├── App.tsx               # 根组件(5路由)
│       ├── main.tsx              # 入口
│       ├── api/                  # API层 (7文件)
│       ├── types/                # 类型定义 (2文件)
│       ├── stores/               # 状态管理 (3文件)
│       ├── hooks/                # 自定义Hook (1文件)
│       └── components/
│           ├── layout/           # 布局组件 (2文件)
│           ├── chat/             # 聊天系统 (1文件)
│           ├── database/         # 数据库面板 (7文件) ★Phase 3
│           ├── documents/        # 文档管理(占位)
│           ├── memories/         # 记忆管理(占位)
│           ├── settings/         # 设置页面(占位)
│           └── shared/           # 共享组件 (3文件)
│
└── data/                         # 运行时数据
    ├── rag_agent.db              # RAG内部数据库
    └── rpa.db                    # RPA目标数据库
```
