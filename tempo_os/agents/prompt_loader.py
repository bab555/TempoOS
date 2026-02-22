# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Prompt Loader — File-based agent prompt routing system.

Loads scene-specific system prompts from .md files in tempo_os/agents/.
Provides a lightweight LLM-based router to classify user intent into
a scene_key, then returns the corresponding prompt and tool set.

Architecture:
  1. _router.md  → intent classification prompt (used by route())
  2. {scene}.md  → scene-specific system prompt
  3. SCENE_TOOLS → per-scene tool subset (not all scenes need all tools)
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("tempo.agents.prompt_loader")

AGENTS_DIR = Path(__file__).parent

KNOWN_SCENES = {"general", "procurement", "document_writing", "data_analysis"}

DEFAULT_SCENE = "general"

# ── Prompt Cache ─────────────────────────────────────────────

_prompt_cache: Dict[str, str] = {}


def _load_prompt(name: str) -> Optional[str]:
    """Load a .md prompt file by name. Returns None if not found."""
    path = AGENTS_DIR / f"{name}.md"
    if not path.exists():
        logger.warning("Prompt file not found: %s", path)
        return None
    return path.read_text(encoding="utf-8").strip()


def _get_prompt(name: str) -> str:
    """Get prompt from cache or load from disk."""
    if name not in _prompt_cache:
        text = _load_prompt(name)
        if text:
            _prompt_cache[name] = text
    return _prompt_cache.get(name, "")


def get_scene_prompt(scene_key: str) -> str:
    """Return the system prompt for a given scene."""
    if scene_key not in KNOWN_SCENES:
        scene_key = DEFAULT_SCENE
    prompt = _get_prompt(scene_key)
    if not prompt:
        prompt = _get_prompt(DEFAULT_SCENE)
    return prompt


def get_router_prompt() -> str:
    """Return the router classification prompt."""
    return _get_prompt("_router")


def reload_prompts() -> None:
    """Clear cache and force reload all prompts from disk."""
    _prompt_cache.clear()
    for name in ["_router"] + list(KNOWN_SCENES):
        _load_prompt(name)
    logger.info("Reloaded %d agent prompts", len(_prompt_cache))


# ── Tool Definitions Per Scene ───────────────────────────────

_TOOL_SEARCH = {
    "type": "function",
    "function": {
        "name": "search",
        "description": "联网搜索：在全网搜索产品信息、价格、供应商、行业资料等外部数据",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词或自然语言查询",
                },
            },
            "required": ["query"],
        },
    },
}

_TOOL_WRITER = {
    "type": "function",
    "function": {
        "name": "writer",
        "description": (
            "智能撰写：生成业务文档。支持的 skill 类型包括——"
            "短文档：quotation(报价表), contract(合同), delivery_note(送货单), "
            "financial_report(财务报表), comparison(比价表), general(通用)；"
            "长文档：tech_doc(技术文档), prd(产品需求文档), "
            "client_doc(客户对接文档), proposal(企划书/方案书)"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "enum": [
                        "quotation", "contract", "delivery_note",
                        "financial_report", "comparison", "general",
                        "tech_doc", "prd", "client_doc", "proposal",
                    ],
                    "description": "撰写技能类型",
                },
                "data": {
                    "type": "object",
                    "description": "业务数据（需求要点、核心内容、参考素材等）",
                },
                "template_id": {
                    "type": "string",
                    "description": "模板记录 ID（用户上传的模板）",
                },
            },
            "required": ["skill"],
        },
    },
}

_TOOL_DATA_QUERY = {
    "type": "function",
    "function": {
        "name": "data_query",
        "description": "内部数据查询：从企业知识库中检索合同、发票、商品、历史文档等内部数据",
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "description": "查询意图（自然语言）",
                },
            },
            "required": ["intent"],
        },
    },
}

ALL_TOOLS = [_TOOL_SEARCH, _TOOL_WRITER, _TOOL_DATA_QUERY]

SCENE_TOOLS: Dict[str, List[Dict[str, Any]]] = {
    "general": ALL_TOOLS,
    "procurement": ALL_TOOLS,
    "document_writing": ALL_TOOLS,
    "data_analysis": ALL_TOOLS,
}


def get_scene_tools(scene_key: str) -> List[Dict[str, Any]]:
    """Return the tool definitions available for a given scene."""
    return SCENE_TOOLS.get(scene_key, ALL_TOOLS)


# ── Router ───────────────────────────────────────────────────


async def route_intent(
    user_message: str,
    api_key: str,
    model: str,
) -> str:
    """
    Classify user intent into a scene_key using a lightweight LLM call.

    Falls back to DEFAULT_SCENE on any error.
    """
    router_prompt = get_router_prompt()
    if not router_prompt:
        return DEFAULT_SCENE

    messages = [
        {"role": "system", "content": router_prompt},
        {"role": "user", "content": user_message},
    ]

    def _sync_call() -> str:
        import dashscope

        response = dashscope.Generation.call(
            model=model,
            messages=messages,
            api_key=api_key,
            result_format="message",
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Router LLM error: {response.code} - {response.message}"
            )
        choice = response.output.choices[0].message
        return (choice.get("content", "") if isinstance(choice, dict) else getattr(choice, "content", "")) or ""

    try:
        raw = await asyncio.to_thread(_sync_call)
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            if len(lines) > 2:
                cleaned = "\n".join(lines[1:-1]).strip()
        parsed = json.loads(cleaned)
        scene = parsed.get("scene", DEFAULT_SCENE)
        if scene not in KNOWN_SCENES:
            logger.warning("Router returned unknown scene '%s', using default", scene)
            return DEFAULT_SCENE
        confidence = parsed.get("confidence", 0)
        logger.info("Route result: scene=%s confidence=%.2f", scene, confidence)
        return scene
    except Exception as e:
        logger.warning("Route intent failed: %s — falling back to '%s'", e, DEFAULT_SCENE)
        return DEFAULT_SCENE


def get_scene_config(scene_key: str) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Convenience: return (system_prompt, tools) for a scene in one call.
    """
    return get_scene_prompt(scene_key), get_scene_tools(scene_key)
