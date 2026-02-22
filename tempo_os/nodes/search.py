# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
SearchNode — Web search via DashScope enable_search capability.

Uses qwen-max with enable_search=True and search_options to perform
real-time web searches. The LLM generates a coherent answer while
the search results (titles, URLs) are captured separately for citation.

DashScope API reference (enable_search):
  - enable_search: True
  - search_options: {"search_strategy": "max", "enable_source": True}
  - Model returns content with inline citations [idx]
  - response.output.search_info.search_results contains source URLs
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

import dashscope

from tempo_os.core.config import settings
from tempo_os.memory.blackboard import TenantBlackboard
from tempo_os.nodes.base import BaseNode, NodeResult

logger = logging.getLogger("tempo.nodes.search")

SEARCH_SYSTEM_PROMPT = """你是一个专业的采购分析助手。当用户要求搜索产品时：
1. 在网上搜索相关产品信息。
2. 对比价格、好评率、规格型号、供应商资质。
3. 以结构化格式返回结果。

输出要求：
- 如果用户要求对比/比价，返回 JSON 格式的表格数据：
  {"type": "table", "title": "...", "columns": [...], "rows": [...]}
- 如果用户是一般性查询，直接返回文字总结。
- columns 格式: [{"key": "field_name", "label": "显示名"}]
- rows 格式: [{"field_name": "value", ...}]

注意：只返回 JSON 或纯文字，不要用 markdown 代码块包裹。"""


class SearchNode(BaseNode):
    """
    Web search node — powered by DashScope enable_search.

    Params accepted from Agent Controller:
      - query (str, required): search query in natural language
      - output_format (str, optional): "table" | "text" (default: auto-detect)
      - search_strategy (str, optional): "turbo" | "max" | "agent" | "agent_max"
    """

    node_id = "search"
    name = "联网搜索"
    description = "使用 DashScope 联网搜索能力，在全网搜索产品信息、价格、供应商等数据"
    param_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词或自然语言查询"},
            "output_format": {"type": "string", "enum": ["table", "text"], "description": "输出格式"},
            "search_strategy": {"type": "string", "enum": ["turbo", "max", "agent", "agent_max"]},
        },
        "required": ["query"],
    }

    async def execute(
        self,
        session_id: str,
        tenant_id: str,
        params: Dict[str, Any],
        blackboard: TenantBlackboard,
    ) -> NodeResult:
        query = params.get("query", "")
        if not query:
            return NodeResult(status="error", error_message="Missing required param: query")

        output_format = params.get("output_format")
        search_strategy = params.get("search_strategy", "max")

        api_key = settings.DASHSCOPE_API_KEY
        model = settings.DASHSCOPE_SEARCH_MODEL

        if not api_key:
            return NodeResult(status="error", error_message="DASHSCOPE_API_KEY not configured")

        # Build messages
        messages = [
            {"role": "system", "content": SEARCH_SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ]

        if output_format == "table":
            messages[-1]["content"] += "\n\n请以表格JSON格式返回对比结果。"

        # Call DashScope with enable_search
        try:
            response_data = await _search_call(
                api_key=api_key,
                model=model,
                messages=messages,
                search_strategy=search_strategy,
            )
        except Exception as e:
            logger.error("SearchNode LLM call failed: %s", e, exc_info=True)
            return NodeResult(status="error", error_message=f"搜索调用失败: {str(e)}")

        if response_data is None:
            return NodeResult(status="error", error_message="搜索调用返回空结果")

        content = response_data.get("content", "")
        search_results = response_data.get("search_results", [])

        # Try to parse structured result from LLM output
        result_data = _parse_search_result(content, search_results)

        # Store in Blackboard: latest for quick access + accumulated for history
        await blackboard.set_state(session_id, "last_search_query", query)
        await blackboard.set_state(session_id, "last_search_result", result_data)
        await blackboard.append_result(session_id, "search", result_data)

        # Build ui_schema based on result type
        ui_schema = _build_search_ui(result_data, search_results)

        return NodeResult(
            status="success",
            result=result_data,
            ui_schema=ui_schema,
            artifacts={"search_result": result_data},
        )


_SEARCH_MAX_RETRIES = 3


async def _search_call(
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    search_strategy: str = "max",
) -> Optional[Dict[str, Any]]:
    """
    Call DashScope Generation API with enable_search=True.
    Retries up to _SEARCH_MAX_RETRIES times with exponential backoff.
    """

    def _sync_call() -> Dict[str, Any]:
        response = dashscope.Generation.call(
            model=model,
            messages=messages,
            api_key=api_key,
            result_format="message",
            enable_search=True,
            search_options={
                "search_strategy": search_strategy,
                "enable_source": True,
            },
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"DashScope search error: {response.code} - {response.message}"
            )

        choice = response.output.choices[0].message

        def _get(obj, key, default=""):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        result: Dict[str, Any] = {
            "content": _get(choice, "content", "") or "",
        }

        search_info = _get(response.output, "search_info", None)
        if search_info:
            raw_results = _get(search_info, "search_results", None)
            if raw_results:
                result["search_results"] = [
                    {
                        "title": _get(web, "title", ""),
                        "url": _get(web, "url", ""),
                        "index": _get(web, "index", ""),
                    }
                    for web in raw_results
                ]

        return result

    last_error: Optional[Exception] = None
    for attempt in range(_SEARCH_MAX_RETRIES):
        try:
            return await asyncio.to_thread(_sync_call)
        except Exception as e:
            last_error = e
            if attempt < _SEARCH_MAX_RETRIES - 1:
                wait = 2 ** attempt
                logger.warning(
                    "Search call failed (attempt %d/%d): %s. Retrying in %ds...",
                    attempt + 1, _SEARCH_MAX_RETRIES, e, wait,
                )
                await asyncio.sleep(wait)

    logger.error("Search call failed after %d attempts: %s", _SEARCH_MAX_RETRIES, last_error)
    raise RuntimeError(f"搜索调用失败 (重试{_SEARCH_MAX_RETRIES}次后): {last_error}") from last_error


def _parse_search_result(
    content: str,
    search_results: List[Dict[str, str]],
) -> Dict[str, Any]:
    """
    Try to parse LLM output as structured JSON (table format).
    Falls back to text result with search references.
    """
    cleaned = content.strip()

    # Strip markdown code block if present
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if len(lines) > 2:
            # Remove first and last lines (```json and ```)
            cleaned = "\n".join(lines[1:-1]).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict) and "type" in parsed:
            # Attach search references
            if search_results:
                parsed["sources"] = search_results
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: wrap as text result
    result: Dict[str, Any] = {
        "type": "text",
        "title": "搜索结果",
        "content": content,
    }
    if search_results:
        result["sources"] = search_results
    return result


def _build_search_ui(
    result_data: Dict[str, Any],
    search_results: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Build A2UI schema from search result."""
    result_type = result_data.get("type", "text")

    if result_type == "table":
        actions = [
            {"label": "导出 Excel", "action_type": "download_json_as_xlsx"},
            {"label": "重新搜索", "action_type": "post_back", "payload": "换一批供应商"},
        ]
        ui: Dict[str, Any] = {
            "component": "smart_table",
            "title": result_data.get("title", "搜索结果"),
            "data": {
                "columns": result_data.get("columns", []),
                "rows": result_data.get("rows", []),
            },
            "actions": actions,
        }
        if search_results:
            ui["data"]["sources"] = search_results
        return ui

    # Text fallback
    return {
        "component": "document_preview",
        "title": result_data.get("title", "搜索结果"),
        "data": {
            "sections": [
                {"title": "搜索结果", "content": result_data.get("content", "")},
            ],
            "sources": search_results,
        },
    }
