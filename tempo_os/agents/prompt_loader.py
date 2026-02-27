# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Prompt Loader — File-based agent prompt routing system.

Loads scene-specific system prompts from .md files in tempo_os/agents/configs/.
Supports YAML frontmatter parsing for decoupled capabilities (Agent.md).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger("tempo.agents.prompt_loader")

AGENTS_DIR = Path(__file__).parent
CONFIGS_DIR = AGENTS_DIR / "configs"

KNOWN_SCENES = {"general", "procurement", "document_writing", "data_analysis"}
DEFAULT_SCENE = "core_agent"  # Changed from general to core_agent


class AgentConfig(BaseModel):
    name: str
    description: str = ""
    model: str = "qwen-max"
    tools: List[str] = Field(default_factory=list)
    system_prompt: str = ""


# ── Prompt Cache ─────────────────────────────────────────────

_agent_cache: Dict[str, AgentConfig] = {}
_router_prompt_cache: str = ""


def _parse_agent_md(content: str) -> AgentConfig:
    """Parse a markdown file with YAML frontmatter."""
    yaml_content = ""
    markdown_content = content
    
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            yaml_content = parts[1].strip()
            markdown_content = parts[2].strip()
            
    try:
        meta = yaml.safe_load(yaml_content) if yaml_content else {}
    except yaml.YAMLError as e:
        logger.error("Failed to parse YAML frontmatter: %s", e)
        meta = {}
        
    return AgentConfig(
        name=meta.get("name", "unknown_agent"),
        description=meta.get("description", ""),
        model=meta.get("model", "qwen-max"),
        tools=meta.get("tools", []),
        system_prompt=markdown_content
    )


def load_agent_config(agent_id: str) -> AgentConfig:
    """Load agent config from configs directory, with caching."""
    if agent_id in _agent_cache:
        return _agent_cache[agent_id]
        
    path = CONFIGS_DIR / f"{agent_id}.md"
    if not path.exists():
        logger.warning("Agent config not found for '%s', falling back to '%s'", agent_id, DEFAULT_SCENE)
        if agent_id != DEFAULT_SCENE:
            return load_agent_config(DEFAULT_SCENE)
        else:
            return AgentConfig(name="core_agent", system_prompt="你是通用数字员工。")
            
    content = path.read_text(encoding="utf-8")
    config = _parse_agent_md(content)
    _agent_cache[agent_id] = config
    return config


def get_router_prompt() -> str:
    """Return the router classification prompt."""
    global _router_prompt_cache
    if not _router_prompt_cache:
        path = AGENTS_DIR / "_router.md"
        if path.exists():
            _router_prompt_cache = path.read_text(encoding="utf-8").strip()
    return _router_prompt_cache


def reload_prompts() -> None:
    """Clear cache and force reload all prompts from disk."""
    _agent_cache.clear()
    global _router_prompt_cache
    _router_prompt_cache = ""
    logger.info("Reloaded agent configs")


# ── Tool Definitions ─────────────────────────────────────────

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

_TOOL_REGISTRY = {
    "search": _TOOL_SEARCH,
    "writer": _TOOL_WRITER,
    "data_query": _TOOL_DATA_QUERY,
}


def get_agent_tools(tool_names: List[str]) -> List[Dict[str, Any]]:
    """Return tool definitions by name."""
    tools = []
    for name in tool_names:
        if name in _TOOL_REGISTRY:
            tools.append(_TOOL_REGISTRY[name])
        else:
            logger.warning("Requested tool '%s' not found in registry", name)
    return tools


# ── Router ───────────────────────────────────────────────────

async def route_intent(
    user_message: str,
    api_key: str,
    model: str,
) -> str:
    """
    Classify user intent into an agent_id (formerly scene_key) using a lightweight LLM call.
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
        msg = response.output.choices[0].message
        try:
            return (msg["content"] if "content" in msg else "") or ""
        except (TypeError, KeyError):
            return getattr(msg, "content", "") or ""

    try:
        raw = await asyncio.to_thread(_sync_call)
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            if len(lines) > 2:
                cleaned = "\n".join(lines[1:-1]).strip()
        parsed = json.loads(cleaned)
        scene = parsed.get("scene", DEFAULT_SCENE)
        
        # Map old scene keys to new agent keys
        scene_to_agent = {
            "general": "core_agent",
            "procurement": "procurement_agent",
            "document_writing": "core_agent", # Will be mapped to a specific agent later
            "data_analysis": "core_agent",
        }
        agent_id = scene_to_agent.get(scene, "core_agent")
        
        confidence = parsed.get("confidence", 0)
        logger.info("Route result: scene=%s (mapped to %s) confidence=%.2f", scene, agent_id, confidence)
        return agent_id
    except Exception as e:
        logger.warning("Route intent failed: %s — falling back to '%s'", e, DEFAULT_SCENE)
        return DEFAULT_SCENE


def get_scene_config(agent_id: str) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Convenience: return (system_prompt, tools) for an agent.
    """
    config = load_agent_config(agent_id)
    return config.system_prompt, get_agent_tools(config.tools)

def list_available_agents() -> List[Dict[str, str]]:
    """List all available agents from configs directory."""
    agents = []
    if CONFIGS_DIR.exists():
        for path in CONFIGS_DIR.glob("*.md"):
            agent_id = path.stem
            cfg = load_agent_config(agent_id)
            agents.append({
                "id": agent_id,
                "name": cfg.name,
                "description": cfg.description,
            })
    return agents
