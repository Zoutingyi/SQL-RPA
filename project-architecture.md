E:\UV\SQL-RPA\project-architecture.md# RAG Agent 项目架构文档

## 项目概述

RAG Agent 是一个智能文档问答系统，支持用户上传文档后进行语义检索和智能问答。系统采用 ReAct 代理循环，集成 6 个 LLM 工具，实现混合检索（语义+关键词）、记忆系统和网络搜索回退。

---

## 技术栈

| 层 | 技术 |
|---|---|
| 后端框架 | FastAPI + Uvicorn (异步) |
| 前端框架 | React 19 + TypeScript 6 |
| 构建工具 | Vite 8 (Rolldown/Rust) |
| 路由 | React Router v7 |
| 状态管理 | Zustand v5 |
| 样式 | Tailwind CSS v4 (暗色/亮色主题) |
| 向量数据库 | Qdrant (本地文件模式) |
| 全文检索 | SQLite FTS5 (BM25 评分) |
| 元数据存储 | SQLite + SQLAlchemy 2.0 (异步) |
| LLM | OpenAI 兼容协议，支持多供应商 |
| 重排序 | Cross-Encoder (BAAI/bge-reranker-v2-m3) |
| 文档解析 | PyMuPDF, python-docx, pandas, PaddleOCR |
| 加密 | AES-256-GCM |

### 支持的 LLM 供应商

DeepSeek / OpenAI / 智谱 (Zhipu) / 月之暗面 (Moonshot) / 通义千问 (Qwen) / Groq / Ollama / 自定义

---

## 目录结构

```
RAG_Agent/
├── main.py                    # 项目入口
├── .env                       # 环境变量 (含加密 API Key)
├── ARCHITECTURE_VIEW.html     # 架构可视化 (Mermaid 图)
├── PROJECT_STATUS.md          # 项目进度 (48 项完成, 4 阶段)
├── TEST_CHECKLIST.md          # 测试清单
│
├── backend/                   # 后端 (59 个 .py 文件)
│   ├── main.py                # FastAPI 应用启动 (路由注册, 中间件)
│   ├── config.py              # Pydantic Settings 配置管理
│   │
│   ├── agent/                 # ReAct 代理系统
│   │   ├── loop.py            # 代理主循环 (SSE 流式, 最大10轮, 120s超时)
│   │   ├── tools.py           # 工具注册中心 (6 个工具)
│   │   ├── classifier.py      # 意图分类 (规则优先 + LLM 回退)
│   │   ├── intercept.py       # 记忆拦截 (正则 + LLM 确认)
│   │   ├── context.py         # 上下文管理 (系统提示词, 消息裁剪)
│   │   └── session_extract.py # 会话后批量提取记忆
│   │
│   ├── api/                   # REST + SSE 接口
│   │   ├── chat.py            # POST /api/chat (SSE 流式聊天)
│   │   ├── documents.py       # 文档上传/列表/删除/重新处理/进度
│   │   ├── conversations.py   # 会话 CRUD
│   │   ├── settings.py        # 设置管理 + 连接测试
│   │   └── memories.py        # 记忆管理 + 画像生成
│   │
│   ├── rag/                   # RAG 管道
│   │   ├── pipeline.py        # 文档摄入主流程 (解析→分块→嵌入→双索引)
│   │   ├── retriever.py       # 混合检索 (语义+BM25→RRF融合→去重→重排序)
│   │   ├── loaders.py         # 文档加载器 (PDF/DOCX/TXT/CSV/XLSX/图片)
│   │   ├── splitter.py        # 文本分割 (tiktoken, 512 token/块, 50 重叠)
│   │   └── progress.py        # 实时进度管理 (发布/订阅)
│   │
│   ├── llm/                   # LLM 抽象层
│   │   ├── base.py            # 抽象基类
│   │   ├── openai_impl.py     # OpenAI 兼容实现
│   │   └── factory.py         # 工厂方法
│   │
│   ├── embedding/             # 嵌入模型抽象层
│   ├── vectordb/              # 向量数据库抽象层 (Qdrant 实现)
│   ├── textdb/                # 全文检索抽象层 (FTS5 实现)
│   ├── reranker/              # 重排序抽象层 (Cross-Encoder)
│   ├── ocr/                   # OCR 抽象层 (PaddleOCR)
│   ├── memory/                # 用户记忆系统
│   │   ├── profile.py         # 画像管理 (单一真相来源, 语义去重)
│   │   └── store.py           # 记忆存储 (Qdrant + SQLite)
│   │
│   ├── models/                # 数据模型
│   │   ├── database.py        # SQLite 初始化 (WAL 模式)
│   │   └── schemas.py         # ORM 模型定义 (5 张表)
│   │
│   ├── middleware/             # 中间件 (请求ID, 访问日志)
│   ├── storage/               # 本地文件存储
│   ├── utils/                 # 加密工具 (AES-256-GCM)
│   ├── worker/                # 后台工作器
│   └── tests/                 # 测试 (12 个测试文件)
│
├── frontend/                  # 前端 (34 个 .ts/.tsx 文件)
│   └── src/
│       ├── App.tsx            # 路由定义 (4 条路由)
│       ├── components/
│       │   ├── chat/          # 聊天系统
│       │   │   ├── ChatPanel.tsx      # 聊天面板容器
│       │   │   ├── ChatInput.tsx      # 输入框 (Token用量, 停止按钮)
│       │   │   ├── MessageList.tsx    # 消息列表 (自动滚动)
│       │   │   ├── MessageBubble.tsx  # 消息气泡 (Markdown, 代码复制)
│       │   │   ├── SourceCard.tsx     # 来源卡片 (可展开预览)
│       │   │   └── ToolCallCard.tsx   # 工具调用卡片 (可折叠)
│       │   ├── documents/     # 文档管理
│       │   │   ├── DocumentList.tsx   # 文档表格列表
│       │   │   ├── UploadZone.tsx     # 拖拽上传区 (实时进度)
│       │   │   └── ChunkViewer.tsx    # 分块查看器 (模态框)
│       │   ├── memories/      # 记忆管理
│       │   │   └── MemoryList.tsx     # 记忆列表 (编辑/删除)
│       │   ├── settings/      # 设置页
│       │   │   └── SettingsPage.tsx   # 供应商/模型/参数/主题
│       │   └── layout/        # 布局
│       │       ├── MainLayout.tsx     # 主布局
│       │       └── Sidebar.tsx        # 侧边栏 (导航+会话列表)
│       ├── stores/            # 状态管理 (Zustand)
│       │   ├── chatStore.ts          # 聊天状态 (258 行)
│       │   ├── documentStore.ts      # 文档状态
│       │   └── toastStore.ts         # 通知状态
│       └── api/               # API 层
│           ├── client.ts             # 通用 HTTP 客户端
│           ├── chat.ts               # SSE 聊天流
│           ├── documents.ts          # 文档 CRUD
│           ├── conversations.ts      # 会话 CRUD
│           └── settings.ts           # 设置 CRUD
│
├── data/                      # 运行时数据 (SQLite 数据库文件)
└── docs/                      # 设计文档 (12 个文件)
```

---

## 数据模型 (5 张核心表)

| 表名 | 主要字段 | 说明 |
|---|---|---|
| `documents` | id(UUID), filename, file_hash, file_size, file_type, status, chunk_count, embedding_model | 文档元数据，file_hash 唯一索引 |
| `conversations` | id(UUID), title, created_at, updated_at, last_extracted_at | 会话记录，last_extracted_at 用于记忆提取触发 |
| `messages` | id(UUID), conversation_id(FK), role, content, tool_call_id, tool_name, tool_args, sources | 聊天消息，支持工具调用和来源引用 |
| `user_memories` | id(UUID), content, memory_type, deprecated, embedding_model, conversation_id | 用户记忆，支持类型标记和废弃管理 |
| `user_profiles` | id(auto), profile_data(JSON), memory_ids(JSON), version, generated_at | 用户画像，version 递增，profile_data 含 name/role/preferences/decisions/facts |
| `chunks_fts` | (虚拟表) | SQLite FTS5 全文索引，unicode61 分词器，CJK 字符分割 |

---

## API 接口

| 路由 | 前缀 | 主要端点 |
|---|---|---|
| Chat | `/api` | `POST /api/chat` — SSE 流式聊天 |
| Documents | `/api/documents` | `POST /upload`, `GET /`, `DELETE /{id}`, `GET /{id}/chunks`, `POST /{id}/reprocess`, `GET /{id}/progress`(SSE) |
| Conversations | `/api/conversations` | `GET /`, `POST /`, `DELETE /{id}`, `PATCH /{id}`, `GET /{id}/messages` |
| Settings | `/api/settings` | `GET /`, `PUT /`, `POST /test-connection` |
| Memories | `/api/memories` | `GET /`, `GET /profile`, `POST /profile/generate`, `PUT /{id}`, `DELETE /{id}`, `DELETE /`(清空) |
| Health | 无 | `GET /api/health` |

---

## Agent 工具链 (6 个已注册工具)

| 工具名 | 功能 | 后端实现 |
|---|---|---|
| `search_docs` | 混合语义检索知识库 | 向量检索+BM25 → RRF融合(k=60) → 去重 → Cross-Encoder重排序 |
| `calculator` | 安全数学表达式求值 | AST 白名单校验 (仅 +-*/) |
| `list_documents` | 列出所有已上传文档 | SQLite 查询 |
| `get_document_info` | 获取指定文档详情 | SQLite 按 ID 查询 |
| `web_search` | 网络搜索 | Bing 优先 (~500ms) → DuckDuckGo 回退 |
| `recall_memory` | 用户画像语义搜索 | Qdrant + numpy 余弦相似度 |

---

## ReAct 代理循环流程

```
用户输入
  ↓
意图分类 (规则优先 → LLM 回退)
  ↓
记忆拦截 (正则提取 + LLM 确认 → 写入画像)
  ↓
画像预加载至系统提示词
  ↓
多轮 ReAct 循环 (最大10轮, 120s超时)
  ├─ LLM 推理 → 决定调用工具
  ├─ 工具执行 → 返回结果
  └─ 结果反馈 → 继续推理 / 生成最终回答
  ↓
消息裁剪 (滑动窗口, 80% token 预算)
  ↓
后台提取记忆 (≥5条新消息触发)
```

---

## 架构设计模式

- **抽象工厂模式**：LLM、Embedding、VectorDB、TextDB、Reranker、OCR 均采用抽象基类+工厂方法，支持灵活切换实现
- **发布/订阅模式**：文档摄入进度通过 asyncio.Queue 实现实时 SSE 推送
- **拦截器模式**：记忆写入通过拦截层控制，LLM 不可直接写入
- **单一真相来源**：用户画像 (UserProfile) 是所有用户数据的权威来源

---

## 数据库 RPA 机器人 — 技术需求

### 核心功能

1. **自然语言 → SQL 生成**：用户在聊天界面描述异常数据特征，LLM 自动生成定位 SQL
2. **数据定位与预览**：执行 SELECT 查询，展示问题数据供用户确认
3. **自动备份**：在执行修改/删除前，将受影响数据导出备份（快照或 INSERT 回滚语句）
4. **SQL 审核机制**：生成的 SQL 在对话框中展示，带执行/取消按钮
5. **敏感操作二次确认**：DROP TABLE / DELETE / TRUNCATE / ALTER 等操作弹出独立审核对话框

### 需新增模块

| 模块 | 路径 | 说明 |
|---|---|---|
| 数据库工具 | `backend/agent/tools/database.py` | `query_db`(只读), `execute_sql`(写操作需审核), `backup_data` |
| 数据库连接器 | `backend/db_connector/` | 抽象层，支持 MySQL/PostgreSQL/SQLite，工厂模式 |
| 操作 API | `backend/api/db_operations.py` | 提交 SQL 审核、获取备份预览、执行确认后 SQL |
| SQL 审核页面 | `frontend/src/components/database/` | 数据预览表、SQL 编辑器、审核确认弹窗、操作日志 |

### 安全设计要点

- **白名单机制**：敏感 SQL 关键字 (DELETE/DROP/TRUNCATE/ALTER) 强制触发审核
- **备份优先**：所有写操作前自动生成回滚 SQL 或数据快照
- **执行权限分级**：
  - SELECT → 直接执行，返回预览
  - INSERT/UPDATE → 确认后执行
  - DELETE/DROP/TRUNCATE/ALTER → 二次确认后执行
- **操作审计**：所有数据库变更记录到 `db_operation_log` 表
