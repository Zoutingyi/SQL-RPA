"""Tool registry for the ReAct agent loop."""

import ast
import asyncio
import logging
import traceback
from dataclasses import dataclass
from abc import ABC, abstractmethod
from typing import Any

from .validation import validate_tool_arguments
from utils.masking import mask_text

logger = logging.getLogger("sql_rpa")


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: str | None = None
    retries: int = 0


class BaseTool(ABC):
    name: str
    description: str
    parameters: dict  # JSON Schema
    max_retries: int = 3
    retry_backoff: float = 1.0
    retry_strategy: str = "exponential"  # "exponential" | "none"

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult: ...

    def to_llm_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool):
        self._tools[tool.name] = tool

    def get_schemas(self) -> list[dict]:
        return [t.to_llm_schema() for t in self._tools.values()]

    async def execute(self, name: str, **kwargs) -> ToolResult:
        tool = self._tools[name]
        try:
            validate_tool_arguments(getattr(tool, "parameters", None), kwargs)
        except ValueError as exc:
            logger.warning(f"Tool {name} rejected invalid arguments: {exc}")
            return ToolResult(
                success=False,
                error=f"Invalid arguments for tool {name}: {exc}",
                retries=0,
            )

        if tool.retry_strategy == "none":
            try:
                result = await tool.execute(**kwargs)
                result.retries = 0
                return result
            except Exception:
                logger.error(f"Tool {name} execution error:\n{traceback.format_exc()}")
                return ToolResult(success=False, error=f"工具 {name} 执行失败，请稍后重试", retries=0)
        elif tool.retry_strategy == "exponential":
            from config import settings

            max_retries = min(tool.max_retries, settings.max_tool_retries)
            for attempt in range(max_retries + 1):
                try:
                    result = await tool.execute(**kwargs)
                    result.retries = attempt
                    return result
                except Exception:
                    if attempt == max_retries:
                        logger.error(f"Tool {name} failed after {attempt} retries:\n{traceback.format_exc()}")
                        return ToolResult(
                            success=False, error=f"工具 {name} 多次重试后仍失败，请稍后重试", retries=attempt
                        )
                    await asyncio.sleep(tool.retry_backoff * (2 ** attempt))
            return ToolResult(success=False, error="max retries exceeded")
        else:
            raise ValueError(f"Unknown retry_strategy: {tool.retry_strategy}")


# ── Stub Tools (implementations to be completed in Phase 1 RPA) ──

class SearchDocsTool(BaseTool):
    name = "search_docs"
    description = "Search the knowledge base for relevant document chunks."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "top_k": {"type": "integer", "description": "Number of results"},
            "document_id": {"type": "string", "description": "Optional: search within a specific document"},
        },
        "required": ["query"],
    }
    async def execute(self, query: str, top_k: int = 0, document_id: str = "") -> ToolResult:
        try:
            from rag.retriever import retriever
            results = await retriever.retrieve(query, top_k=top_k, document_id=document_id)
            return ToolResult(success=True, data={
                "count": len(results),
                "results": [
                    {
                        "chunk_id": r.chunk_id,
                        "document_id": r.document_id,
                        "text": mask_text(r.text),
                        "score": r.score,
                        "source": r.source,
                    }
                    for r in results
                ],
            })
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class CalculatorTool(BaseTool):
    name = "calculator"
    description = "Evaluate a mathematical expression."
    parameters = {
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "Math expression with +-*/()"},
        },
        "required": ["expression"],
    }
    max_retries = 0
    retry_strategy = "none"

    async def execute(self, expression: str) -> ToolResult:
        try:
            tree = ast.parse(expression.strip(), mode="eval")
        except SyntaxError as e:
            return ToolResult(success=False, error=f"Syntax error: {e.msg}")

        if not self._is_allowed(tree.body):
            return ToolResult(success=False, error="Only + - * / ( ) allowed")

        try:
            value = self._eval_node(tree.body)
            return ToolResult(success=True, data={"expression": expression, "result": value})
        except ZeroDivisionError:
            return ToolResult(success=False, error="Division by zero")
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def _is_allowed(self, node):
        if isinstance(node, ast.Constant):
            return isinstance(node.value, (int, float))
        if isinstance(node, ast.UnaryOp):
            return isinstance(node.op, ast.USub) and self._is_allowed(node.operand)
        if isinstance(node, ast.BinOp):
            return (
                isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div))
                and self._is_allowed(node.left)
                and self._is_allowed(node.right)
            )
        return False

    def _eval_node(self, node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.UnaryOp):
            return -self._eval_node(node.operand)
        if isinstance(node, ast.BinOp):
            left = self._eval_node(node.left)
            right = self._eval_node(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            elif isinstance(node.op, ast.Sub):
                return left - right
            elif isinstance(node.op, ast.Mult):
                return left * right
            elif isinstance(node.op, ast.Div):
                return left / right
        raise ValueError(f"Unexpected node: {type(node)}")


class ListDocumentsTool(BaseTool):
    name = "list_documents"
    description = "List all documents in the knowledge base."
    parameters = {"type": "object", "properties": {}, "required": []}
    async def execute(self) -> ToolResult:
        try:
            from sqlalchemy import select
            from auth import get_tenant_id
            from organization_context import get_resource_scope
            from agent.identity import get_actor_id
            from models.database import async_session
            from models.schemas import Document
            async with async_session() as session:
                scope_ids, owner_only = await get_resource_scope(
                    get_tenant_id(), user_id=get_actor_id())
                query = select(Document).where(Document.tenant_id.in_(scope_ids))
                if owner_only:
                    query = query.where(Document.owner_id == get_actor_id())
                result = await session.execute(query.order_by(Document.created_at.desc()))
                docs = result.scalars().all()
            return ToolResult(success=True, data={
                "count": len(docs),
                "documents": [
                    {
                        "id": d.id,
                        "filename": d.filename,
                        "file_type": d.file_type,
                        "status": d.status.value if hasattr(d.status, "value") else d.status,
                        "chunk_count": d.chunk_count,
                        "file_size": d.file_size,
                        "created_at": d.created_at.isoformat() if d.created_at else "",
                    }
                    for d in docs
                ],
            })
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class GetDocumentInfoTool(BaseTool):
    name = "get_document_info"
    description = "Get detailed information about a specific document."
    parameters = {
        "type": "object",
        "properties": {"document_id": {"type": "string", "description": "Document ID"}},
        "required": ["document_id"],
    }
    async def execute(self, document_id: str) -> ToolResult:
        try:
            from sqlalchemy import select
            from auth import get_tenant_id
            from organization_context import get_resource_scope
            from agent.identity import get_actor_id
            from models.database import async_session
            from models.schemas import Document
            async with async_session() as session:
                scope_ids, owner_only = await get_resource_scope(
                    get_tenant_id(), user_id=get_actor_id())
                query = select(Document).where(
                    Document.id == document_id, Document.tenant_id.in_(scope_ids))
                if owner_only:
                    query = query.where(Document.owner_id == get_actor_id())
                doc = await session.scalar(query)
                if not doc:
                    return ToolResult(success=False, error=f"Document {document_id} not found")
                return ToolResult(success=True, data={
                    "id": doc.id,
                    "filename": doc.filename,
                    "file_size": doc.file_size,
                    "file_type": doc.file_type,
                    "status": doc.status.value if hasattr(doc.status, "value") else doc.status,
                    "chunk_count": doc.chunk_count,
                    "error_message": doc.error_message,
                    "embedding_model": doc.embedding_model,
                    "embedding_dim": doc.embedding_dim,
                    "created_at": doc.created_at.isoformat() if doc.created_at else "",
                })
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class WebSearchTool(BaseTool):
    name = "web_search"
    description = (
        "Search the internet for real-time information. "
        "Use this when the knowledge base does not contain relevant answers, "
        "or when the user asks about current events, news, or external facts. "
        "Returns result titles, snippets, and source URLs."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query in natural language"},
            "max_results": {"type": "integer", "description": "Max results (default 5, max 10)"},
        },
        "required": ["query"],
    }
    max_retries = 1
    retry_strategy = "exponential"

    async def execute(self, query: str, max_results: int = 0) -> ToolResult:
        from config import settings
        if not settings.web_search_enabled:
            return ToolResult(
                success=False,
                error="Web search is disabled. Enable web_search_enabled in settings to use this feature.",
            )

        max_r = max_results or settings.web_search_max_results
        max_r = min(max_r, 10)

        try:
            from duckduckgo_search import DDGS

            results = []
            ddgs_kwargs = {}
            if settings.web_search_proxy:
                ddgs_kwargs["proxy"] = settings.web_search_proxy
            with DDGS(**ddgs_kwargs) as ddgs:
                for r in ddgs.text(query, max_results=max_r):
                    results.append({
                        "title": r.get("title", ""),
                        "body": r.get("body", ""),
                        "href": r.get("href", ""),
                    })

            if not results:
                return ToolResult(
                    success=False,
                    error=f"No results found for: {query}",
                )

            return ToolResult(success=True, data={
                "query": query,
                "count": len(results),
                "results": results,
            })
        except ImportError:
            return ToolResult(
                success=False,
                error="Web search library not installed. Run: pip install duckduckgo_search",
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Web search error: {str(e)}")


class RecallMemoryTool(BaseTool):
    name = "recall_memory"
    description = "Search the user's long-term memory for personal facts, preferences, decisions, and identity information."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "top_k": {"type": "integer", "description": "Number of memories"},
        },
        "required": ["query"],
    }
    async def execute(self, query: str, top_k: int = 5) -> ToolResult:
        from memory.store import MemoryStore
        store = MemoryStore()
        try:
            results = await store.search_memories(query, top_k=top_k)
            return ToolResult(
                success=True,
                data={
                    "count": len(results),
                    "results": [{"content": r["content"], "memory_type": r["memory_type"]} for r in results],
                },
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# ── Global registry ──

registry = ToolRegistry()
registry.register(SearchDocsTool())
registry.register(CalculatorTool())
registry.register(ListDocumentsTool())
registry.register(GetDocumentInfoTool())
registry.register(WebSearchTool())
registry.register(RecallMemoryTool())

# RPA database tools (Phase 1)
from .database import GetDbSchemaTool, QueryDbTool, ExecuteSqlTool
registry.register(GetDbSchemaTool())
registry.register(QueryDbTool())
registry.register(ExecuteSqlTool())
