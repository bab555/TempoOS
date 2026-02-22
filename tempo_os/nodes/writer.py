# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
WriterNode — Intelligent document writer powered by Skill Prompts.

Supports two modes:
  1. **Short-form** (quotation, contract, delivery_note, financial_report,
     comparison, general) — single LLM call with a .txt skill prompt.
  2. **Long-form** (tech_doc, prd, client_doc, proposal) — multi-step
     generation: outline → chapter-by-chapter writing → assembly.
     Uses .md skill prompts that define the full writing workflow.

Skill Prompts are the core extensibility mechanism — adding a new
document type only requires adding a new prompt file to the skills/ dir
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

SHORT_FORM_SKILLS = {
    "quotation",
    "contract",
    "delivery_note",
    "financial_report",
    "comparison",
    "general",
}

LONG_FORM_SKILLS = {
    "tech_doc",
    "prd",
    "client_doc",
    "proposal",
}

SKILL_KEYS = SHORT_FORM_SKILLS | LONG_FORM_SKILLS


def _load_skill_prompt(skill_key: str) -> Optional[str]:
    """Load skill prompt from file (.md for long-form, .txt for short-form)."""
    for ext in (".md", ".txt"):
        path = SKILLS_DIR / f"{skill_key}{ext}"
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    logger.warning("Skill prompt file not found for: %s", skill_key)
    return None


_SKILL_CACHE: Dict[str, str] = {}
for _key in SKILL_KEYS:
    _prompt = _load_skill_prompt(_key)
    if _prompt:
        _SKILL_CACHE[_key] = _prompt


class WriterNode(BaseNode):
    """
    Intelligent document writer node.

    Params from Agent Controller (via LLM Tool Use):
      - skill (str, required): skill key
      - data (dict, optional): structured business data / requirements
      - template_id (str, optional): Tonglu DataRecord ID of an uploaded template
    """

    node_id = "writer"
    name = "智能撰写"
    description = (
        "根据 Skill Prompt 和业务数据生成文档。"
        "短文档（报价表/合同/送货单等）单次生成；"
        "长文档（技术文档/PRD/企划书等）自动执行大纲→逐章撰写→拼接的完整流程"
    )
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
                "description": "业务数据 / 需求要点 / 参考素材",
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

        skill_prompt = _SKILL_CACHE.get(skill_key)
        if not skill_prompt:
            skill_prompt = _load_skill_prompt(skill_key)
        if not skill_prompt:
            skill_prompt = _SKILL_CACHE.get("general", "请根据数据生成对应的业务文档。")
            logger.warning("Skill '%s' not found, falling back to 'general'", skill_key)

        context_parts = await _gather_context(blackboard, session_id, template_id, data)

        if not context_parts and not data:
            return NodeResult(
                status="need_user_input",
                error_message="缺少业务数据，请提供相关数据或上传文件后重试。",
                result={"message": "请提供业务数据"},
            )

        api_key = settings.DASHSCOPE_API_KEY
        model = settings.DASHSCOPE_MODEL

        if not api_key:
            return NodeResult(status="error", error_message="DASHSCOPE_API_KEY not configured")

        # Route to long-form or short-form generation
        if skill_key in LONG_FORM_SKILLS:
            return await self._execute_long_form(
                skill_key=skill_key,
                skill_prompt=skill_prompt,
                context_parts=context_parts,
                api_key=api_key,
                model=model,
                session_id=session_id,
                blackboard=blackboard,
            )
        else:
            return await self._execute_short_form(
                skill_key=skill_key,
                skill_prompt=skill_prompt,
                context_parts=context_parts,
                api_key=api_key,
                model=model,
                session_id=session_id,
                blackboard=blackboard,
            )

    # ── Short-form: single LLM call ─────────────────────────

    async def _execute_short_form(
        self,
        skill_key: str,
        skill_prompt: str,
        context_parts: List[str],
        api_key: str,
        model: str,
        session_id: str,
        blackboard: TenantBlackboard,
    ) -> NodeResult:
        context_block = "\n\n---\n\n".join(context_parts) if context_parts else ""
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": skill_prompt},
            {"role": "user", "content": context_block},
        ]

        try:
            content = await _writer_call(api_key=api_key, model=model, messages=messages)
        except Exception as e:
            logger.error("WriterNode short-form LLM call failed: %s", e, exc_info=True)
            return NodeResult(status="error", error_message=f"撰写调用失败: {str(e)}")

        if not content:
            return NodeResult(status="error", error_message="LLM 返回空内容")

        result_data = _parse_writer_output(content, skill_key)

        artifact_key = f"{skill_key}_result"
        await blackboard.set_state(session_id, f"last_{artifact_key}", result_data)

        ui_schema = _build_writer_ui(result_data, skill_key)

        return NodeResult(
            status="success",
            result=result_data,
            ui_schema=ui_schema,
            artifacts={artifact_key: result_data},
        )

    # ── Long-form: outline → chapter-by-chapter → assemble ──

    async def _execute_long_form(
        self,
        skill_key: str,
        skill_prompt: str,
        context_parts: List[str],
        api_key: str,
        model: str,
        session_id: str,
        blackboard: TenantBlackboard,
    ) -> NodeResult:
        context_block = "\n\n---\n\n".join(context_parts) if context_parts else ""

        # Step 1: Generate outline
        outline_prompt = (
            f"{skill_prompt}\n\n"
            "---\n\n"
            "现在请执行【步骤一】：根据以下需求信息生成文档大纲。\n"
            "只输出大纲 JSON（包含 outline 数组），不要写正文。"
        )
        outline_messages: List[Dict[str, Any]] = [
            {"role": "system", "content": outline_prompt},
            {"role": "user", "content": context_block},
        ]

        try:
            outline_raw = await _writer_call(api_key=api_key, model=model, messages=outline_messages)
        except Exception as e:
            logger.error("Long-form outline generation failed: %s", e, exc_info=True)
            return NodeResult(status="error", error_message=f"大纲生成失败: {str(e)}")

        outline_data = _parse_outline(outline_raw)
        if not outline_data:
            logger.warning("Failed to parse outline, falling back to short-form")
            return await self._execute_short_form(
                skill_key, skill_prompt, context_parts, api_key, model, session_id, blackboard,
            )

        await blackboard.set_state(session_id, f"{skill_key}_outline", outline_data)
        logger.info("Generated outline with %d chapters for skill=%s", len(outline_data), skill_key)

        # Step 2: Write each chapter
        outline_summary = json.dumps(outline_data, ensure_ascii=False, indent=2)
        all_sections: List[Dict[str, Any]] = []
        previous_summary = ""

        for i, chapter in enumerate(outline_data):
            chapter_title = chapter.get("title", f"第{i+1}章")
            key_points = chapter.get("key_points", [])
            key_points_text = "\n".join(f"- {p}" for p in key_points) if key_points else "（无具体要点）"

            chapter_prompt = (
                f"{skill_prompt}\n\n"
                "---\n\n"
                "现在请执行【步骤二】：撰写文档的一个章节。\n\n"
                f"## 完整大纲\n```json\n{outline_summary}\n```\n\n"
                f"## 当前要撰写的章节\n"
                f"- 章节序号：第 {i+1} 章（共 {len(outline_data)} 章）\n"
                f"- 章节标题：{chapter_title}\n"
                f"- 核心要点：\n{key_points_text}\n\n"
            )
            if previous_summary:
                chapter_prompt += f"## 前文摘要\n{previous_summary}\n\n"

            chapter_prompt += (
                "## 要求\n"
                "只撰写本章节的内容，以 Markdown 格式输出正文。\n"
                "不要输出 JSON，不要重复大纲，只输出本章节的标题和正文。"
            )

            chapter_messages: List[Dict[str, Any]] = [
                {"role": "system", "content": chapter_prompt},
                {"role": "user", "content": context_block},
            ]

            try:
                chapter_content = await _writer_call(api_key=api_key, model=model, messages=chapter_messages)
            except Exception as e:
                logger.error("Chapter %d writing failed: %s", i + 1, e, exc_info=True)
                chapter_content = f"（第 {i+1} 章 '{chapter_title}' 生成失败：{e!s}）"

            all_sections.append({
                "title": chapter_title,
                "content": chapter_content or "",
                "level": 1,
            })

            # Build a running summary of completed chapters for context continuity
            if chapter_content:
                summary_lines = chapter_content.strip().split("\n")[:3]
                previous_summary += f"\n### {chapter_title}\n" + "\n".join(summary_lines) + "\n..."

            logger.info("Completed chapter %d/%d: %s", i + 1, len(outline_data), chapter_title)

        # Step 3: Assemble final document
        result_data: Dict[str, Any] = {
            "type": "document",
            "title": _skill_title(skill_key),
            "skill": skill_key,
            "meta": {
                "doc_type": skill_key,
                "version": "1.0",
                "author": "数字员工助手",
                "total_chapters": len(outline_data),
            },
            "outline": outline_data,
            "sections": all_sections,
            "fields": {},
        }

        # Try to extract title from first section or outline
        if outline_data:
            first_title = outline_data[0].get("title", "")
            if first_title:
                result_data["title"] = f"{_skill_title(skill_key)} — {first_title.split('—')[0].strip()}"

        artifact_key = f"{skill_key}_result"
        await blackboard.set_state(session_id, f"last_{artifact_key}", result_data)

        ui_schema = _build_long_form_ui(result_data, skill_key)

        return NodeResult(
            status="success",
            result=result_data,
            ui_schema=ui_schema,
            artifacts={artifact_key: result_data},
        )


# ── LLM Call with Retry ──────────────────────────────────────

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
        choice = response.output.choices[0].message
        return (choice.get("content", "") if isinstance(choice, dict) else getattr(choice, "content", "")) or ""

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


# ── Context Gathering ────────────────────────────────────────


async def _gather_context(
    blackboard: TenantBlackboard,
    session_id: str,
    template_id: Optional[str],
    data: Dict[str, Any],
) -> List[str]:
    """Gather all available context from Blackboard and params."""
    context_parts: List[str] = []

    search_result = await blackboard.get_state(session_id, "last_search_result")
    if search_result:
        context_parts.append(
            f"搜索结果数据:\n{json.dumps(search_result, ensure_ascii=False, indent=2)}"
        )

    data_query_result = await blackboard.get_state(session_id, "last_data_query_result")
    if data_query_result:
        context_parts.append(
            f"知识库查询结果:\n{json.dumps(data_query_result, ensure_ascii=False, indent=2)}"
        )

    template_text = None
    if template_id:
        template_text = await blackboard.get_state(session_id, f"template:{template_id}")
    if not template_text:
        template_text = await blackboard.get_state(session_id, "last_template_content")
    if template_text:
        context_parts.append(f"模板内容:\n{template_text}")

    if data:
        context_parts.append(
            f"业务数据/需求信息:\n{json.dumps(data, ensure_ascii=False, indent=2)}"
        )

    return context_parts


# ── Parsing ──────────────────────────────────────────────────


def _parse_outline(raw: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    """Parse outline JSON from LLM output. Returns list of chapters or None."""
    if not raw:
        return None

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if len(lines) > 2:
            cleaned = "\n".join(lines[1:-1]).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict) and "outline" in parsed:
            outline = parsed["outline"]
            if isinstance(outline, list) and len(outline) > 0:
                return outline
        if isinstance(parsed, list) and len(parsed) > 0:
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    return None


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

    return {
        "type": "document",
        "title": _skill_title(skill_key),
        "skill": skill_key,
        "sections": [{"title": "内容", "content": content}],
        "fields": {},
    }


# ── UI Schema Builders ───────────────────────────────────────


def _build_writer_ui(result_data: Dict[str, Any], skill_key: str) -> Dict[str, Any]:
    """Build A2UI schema from short-form writer result."""
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


def _build_long_form_ui(result_data: Dict[str, Any], skill_key: str) -> Dict[str, Any]:
    """Build A2UI schema for long-form documents with outline + sections."""
    sections = result_data.get("sections", [])
    outline = result_data.get("outline", [])

    return {
        "component": "document_preview",
        "title": result_data.get("title", _skill_title(skill_key)),
        "data": {
            "fields": result_data.get("fields", {}),
            "sections": sections,
            "outline": outline,
            "meta": result_data.get("meta", {}),
        },
        "actions": [
            {"label": "下载 Word", "action_type": "download_generated_file"},
            {"label": "修改章节", "action_type": "post_back", "payload": "我想修改这份文档的某个章节"},
        ],
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
        "tech_doc": "技术文档",
        "prd": "产品需求文档",
        "client_doc": "客户对接文档",
        "proposal": "企划书",
    }
    return titles.get(skill_key, "文档")
