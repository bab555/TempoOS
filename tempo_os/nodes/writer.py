# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
WriterNode — Intelligent document writer powered by Skill Prompts.

Uses qwen3-max to generate structured business documents (quotations,
contracts, delivery notes, financial reports, etc.) based on:
  1. A Skill Prompt (loaded from tempo_os/nodes/skills/<skill_key>.txt)
  2. Business data passed in params or read from Blackboard
  3. Optional template content (previously parsed by Tonglu)

Skill Prompts are the core extensibility mechanism — adding a new
document type only requires adding a new .txt file to the skills/ dir
and registering its key in SKILL_KEYS.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import dashscope

from tempo_os.core.config import settings
from tempo_os.memory.blackboard import TenantBlackboard
from tempo_os.nodes.base import BaseNode, NodeResult

logger = logging.getLogger("tempo.nodes.writer")

SKILLS_DIR = Path(__file__).parent / "skills"

SKILL_KEYS = {
    "quotation",
    "contract",
    "delivery_note",
    "financial_report",
    "comparison",
    "general",
}


def _load_skill_prompt(skill_key: str) -> Optional[str]:
    """Load skill prompt text from file. Returns None if not found."""
    path = SKILLS_DIR / f"{skill_key}.txt"
    if not path.exists():
        logger.warning("Skill prompt file not found: %s", path)
        return None
    return path.read_text(encoding="utf-8").strip()


# Pre-load all skills at import time for performance
_SKILL_CACHE: Dict[str, str] = {}
for _key in SKILL_KEYS:
    _prompt = _load_skill_prompt(_key)
    if _prompt:
        _SKILL_CACHE[_key] = _prompt


class WriterNode(BaseNode):
    """
    Intelligent document writer node.

    Params from Agent Controller (via LLM Tool Use):
      - skill (str, required): skill key — "quotation" | "contract" | "delivery_note" |
            "financial_report" | "comparison" | "general"
      - data (dict, optional): structured business data to fill the document
      - template_id (str, optional): Tonglu DataRecord ID of an uploaded template
    """

    node_id = "writer"
    name = "智能撰写"
    description = "根据 Skill Prompt 和业务数据，使用 LLM 生成报价表、合同、送货单、财务报表等"
    param_schema = {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "enum": list(SKILL_KEYS),
                "description": "撰写技能类型",
            },
            "data": {
                "type": "object",
                "description": "业务数据",
            },
            "template_id": {
                "type": "string",
                "description": "模板记录 ID（Tonglu DataRecord）",
            },
        },
        "required": ["skill"],
    }

    async def execute(
        self,
        session_id: str,
        tenant_id: str,
        params: Dict[str, Any],
        blackboard: TenantBlackboard,
    ) -> NodeResult:
        skill_key = params.get("skill", "general")
        data = params.get("data") or {}
        template_id = params.get("template_id")

        # 1. Load skill prompt
        skill_prompt = _SKILL_CACHE.get(skill_key)
        if not skill_prompt:
            skill_prompt = _load_skill_prompt(skill_key)
        if not skill_prompt:
            skill_prompt = _SKILL_CACHE.get("general", "请根据数据生成对应的业务文档。")
            logger.warning("Skill '%s' not found, falling back to 'general'", skill_key)

        # 2. Gather context from Blackboard (search results, file contents, etc.)
        context_parts: List[str] = []

        # Previous search results
        search_result = await blackboard.get_state(session_id, "last_search_result")
        if search_result:
            context_parts.append(
                f"搜索结果数据:\n{json.dumps(search_result, ensure_ascii=False, indent=2)}"
            )

        # Template content (if template_id provided or stored in Blackboard)
        template_text = None
        if template_id:
            template_text = await blackboard.get_state(session_id, f"template:{template_id}")
        if not template_text:
            template_text = await blackboard.get_state(session_id, "last_template_content")
        if template_text:
            context_parts.append(f"模板内容:\n{template_text}")

        # Inline data from params
        if data:
            context_parts.append(
                f"业务数据:\n{json.dumps(data, ensure_ascii=False, indent=2)}"
            )

        if not context_parts and not data:
            return NodeResult(
                status="need_user_input",
                error_message="缺少业务数据，请提供相关数据或上传文件后重试。",
                result={"message": "请提供业务数据"},
            )

        # 3. Build LLM messages
        context_block = "\n\n---\n\n".join(context_parts) if context_parts else ""
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": skill_prompt},
            {"role": "user", "content": context_block},
        ]

        # 4. Call LLM (qwen3-max)
        api_key = settings.DASHSCOPE_API_KEY
        model = settings.DASHSCOPE_MODEL  # qwen3-max

        if not api_key:
            return NodeResult(status="error", error_message="DASHSCOPE_API_KEY not configured")

        try:
            content = await _writer_call(api_key=api_key, model=model, messages=messages)
        except Exception as e:
            logger.error("WriterNode LLM call failed: %s", e, exc_info=True)
            return NodeResult(status="error", error_message=f"撰写调用失败: {str(e)}")

        if not content:
            return NodeResult(status="error", error_message="LLM 返回空内容")

        # 5. Parse structured output
        result_data = _parse_writer_output(content, skill_key)

        # 6. Store in Blackboard
        artifact_key = f"{skill_key}_result"
        await blackboard.set_state(session_id, f"last_{artifact_key}", result_data)

        # 7. Build UI schema
        ui_schema = _build_writer_ui(result_data, skill_key)

        return NodeResult(
            status="success",
            result=result_data,
            ui_schema=ui_schema,
            artifacts={artifact_key: result_data},
        )


_WRITER_MAX_RETRIES = 3


async def _writer_call(
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
) -> Optional[str]:
    """Call DashScope Generation API for document writing with retry."""

    def _sync_call() -> str:
        response = dashscope.Generation.call(
            model=model,
            messages=messages,
            api_key=api_key,
            result_format="message",
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"DashScope writer error: {response.code} - {response.message}"
            )
        return response.output.choices[0].message.content

    last_error: Optional[Exception] = None
    for attempt in range(_WRITER_MAX_RETRIES):
        try:
            return await asyncio.to_thread(_sync_call)
        except Exception as e:
            last_error = e
            if attempt < _WRITER_MAX_RETRIES - 1:
                wait = 2 ** attempt
                logger.warning(
                    "Writer call failed (attempt %d/%d): %s. Retrying in %ds...",
                    attempt + 1, _WRITER_MAX_RETRIES, e, wait,
                )
                await asyncio.sleep(wait)

    logger.error("Writer call failed after %d attempts: %s", _WRITER_MAX_RETRIES, last_error)
    raise RuntimeError(f"撰写调用失败 (重试{_WRITER_MAX_RETRIES}次后): {last_error}") from last_error


def _parse_writer_output(content: str, skill_key: str) -> Dict[str, Any]:
    """Parse LLM output as structured JSON, with fallback."""
    cleaned = content.strip()

    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if len(lines) > 2:
            cleaned = "\n".join(lines[1:-1]).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            parsed.setdefault("skill", skill_key)
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: wrap as document
    return {
        "type": "document",
        "title": _skill_title(skill_key),
        "skill": skill_key,
        "sections": [{"title": "内容", "content": content}],
        "fields": {},
    }


def _build_writer_ui(result_data: Dict[str, Any], skill_key: str) -> Dict[str, Any]:
    """Build A2UI schema from writer result."""
    result_type = result_data.get("type", "document")

    if result_type == "table":
        actions = [
            {"label": "导出 Excel", "action_type": "download_json_as_xlsx"},
        ]
        if skill_key == "quotation":
            actions.append({"label": "生成合同", "action_type": "post_back", "payload": "根据这份报价表生成合同"})

        return {
            "component": "smart_table",
            "title": result_data.get("title", _skill_title(skill_key)),
            "data": {
                "columns": result_data.get("columns", []),
                "rows": result_data.get("rows", []),
                "meta": result_data.get("meta", {}),
                "summary": result_data.get("summary", {}),
            },
            "actions": actions,
        }

    if result_type == "report":
        return {
            "component": "chart_report",
            "title": result_data.get("title", _skill_title(skill_key)),
            "data": {
                "metrics": result_data.get("metrics", []),
                "charts": result_data.get("charts", []),
                "tables": result_data.get("tables", []),
                "meta": result_data.get("meta", {}),
            },
        }

    # document / document_fill / fallback
    actions = [
        {"label": "下载 Word", "action_type": "download_generated_file"},
    ]
    if skill_key == "contract":
        actions.append({"label": "生成送货单", "action_type": "post_back", "payload": "根据这份合同生成送货单"})

    return {
        "component": "document_preview",
        "title": result_data.get("title", _skill_title(skill_key)),
        "data": {
            "fields": result_data.get("fields", {}),
            "sections": result_data.get("sections", []),
            "meta": result_data.get("meta", {}),
        },
        "actions": actions,
    }


def _skill_title(skill_key: str) -> str:
    """Human-readable title for a skill."""
    titles = {
        "quotation": "报价表",
        "contract": "采购合同",
        "delivery_note": "送货单",
        "financial_report": "财务报表",
        "comparison": "比价对比表",
        "general": "业务文档",
    }
    return titles.get(skill_key, "文档")
