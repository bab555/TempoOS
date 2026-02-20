# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Agent Controller — Central LLM-driven chat endpoint with SSE streaming.

This is the primary entry point for frontend interaction. The LLM acts as
a central controller that decides whether to:
  - Reply with plain text (left-side chat bubble)
  - Invoke a tool/node (backend execution, then push UI result)
  - Request more information from the user

All output is streamed via Server-Sent Events (SSE) with distinct event
types so the frontend can route content to the correct UI region:
  - event: message     → left-side conversation bubble (streamed text)
  - event: ui_render   → right-side visualization panel (A2UI component)
  - event: thinking    → loading/status indicator
  - event: done        → end of response
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from tempo_os.api.deps import get_current_tenant
from tempo_os.api.sse import (
    sse_done,
    sse_error,
    sse_event,
    sse_thinking,
)
from tempo_os.core.context import get_platform_context
from tempo_os.core.tenant import TenantContext
from tempo_os.protocols.events import FILE_UPLOADED, FILE_READY
from tempo_os.protocols.schema import TempoEvent

logger = logging.getLogger("tempo.api.agent")

router = APIRouter(prefix="/agent", tags=["agent"])


# ── Request / Response Models ────────────────────────────────


class FileRef(BaseModel):
    """Reference to a file uploaded to OSS."""
    name: str = Field(..., description="Display file name")
    url: str = Field(..., description="OSS URL of the uploaded file")
    type: str = Field(default="", description="MIME type")


class UserMessage(BaseModel):
    """A single message in the conversation."""
    role: str = Field(default="user", description="Message role: user / assistant / system")
    content: str = Field(..., description="Text content")
    files: List[FileRef] = Field(default_factory=list, description="Attached files (OSS URLs)")


class AgentChatRequest(BaseModel):
    """Request body for POST /api/agent/chat."""
    session_id: Optional[str] = Field(None, description="Session ID (empty on first call, returned by server)")
    messages: List[UserMessage] = Field(..., min_length=1, description="Conversation messages")
    context: Optional[Dict[str, Any]] = Field(None, description="Optional frontend context (current_page, etc.)")


# ── Tool Definitions (for LLM Function Calling) ─────────────


AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "联网搜索：在全网搜索产品信息、价格、供应商等外部数据",
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
    },
    {
        "type": "function",
        "function": {
            "name": "writer",
            "description": "智能撰写：生成报价表、合同、送货单、财务报表等业务文档",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "enum": ["quotation", "contract", "delivery_note", "financial_report", "comparison", "general"],
                        "description": "撰写技能类型",
                    },
                    "data": {
                        "type": "object",
                        "description": "业务数据（如报价清单、合同信息等）",
                    },
                    "template_id": {
                        "type": "string",
                        "description": "模板记录 ID（用户上传的模板）",
                    },
                },
                "required": ["skill"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "data_query",
            "description": "内部数据查询：从企业知识库中检索合同、发票、商品等内部数据",
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
    },
]


# ── System Prompt ────────────────────────────────────────────

SYSTEM_PROMPT = """你是"数字员工助手"，一个专业的企业办公AI助手。你的核心能力包括：

1. **联网搜索 (search)**：在全网搜索产品信息、价格、供应商数据，生成比价表。
2. **智能撰写 (writer)**：根据数据和模板生成报价表、采购合同、送货单、财务报表等业务文档。
3. **内部数据查询 (data_query)**：从企业知识库中检索历史合同、发票、商品 SKU 等内部数据。

工作原则：
- 用户的需求可能需要你调用一个或多个工具来完成。
- 先理解用户意图，必要时追问细节，然后选择合适的工具执行。
- 执行完毕后，用简洁的语言总结结果。
- 如果用户上传了文件，注意利用文件内容来辅助完成任务。
"""


# ── Endpoint ─────────────────────────────────────────────────


@router.post("/chat")
async def agent_chat(
    req: AgentChatRequest,
    request: Request,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """
    Central Agent chat endpoint with SSE streaming.

    This is the main entry point for all frontend interactions.
    The LLM decides what tools to call based on user input.
    """
    session_id = req.session_id or str(uuid.uuid4())

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            ui_default_id = "panel_main"
            # 1. Emit session init
            yield sse_event("session_init", {"session_id": session_id})

            # 2. Pre-process files: send to Tonglu via EventBus, wait for text
            ctx = get_platform_context()
            bb = ctx.get_blackboard(tenant.tenant_id)

            # Store session params in Blackboard for Node access
            await bb.set_state(session_id, "_tenant_id", tenant.tenant_id)
            if tenant.user_id:
                await bb.set_state(session_id, "_user_id", tenant.user_id)

            # Collect all files from the latest user message
            all_files = _collect_files(req.messages)
            file_texts: Dict[str, str] = {}  # url -> parsed text

            if all_files:
                yield sse_event(
                    "thinking",
                    {"content": "正在处理上传文件...", "phase": "file_processing", "status": "running", "progress": 2},
                )
                file_texts = await _process_files_via_event_bus(
                    files=all_files,
                    tenant_id=tenant.tenant_id,
                    session_id=session_id,
                    user_id=tenant.user_id,
                    ctx=ctx,
                    bb=bb,
                )

            # 3. Build messages for LLM (with file text content injected)
            llm_messages = _build_llm_messages(req.messages, file_texts)

            from tempo_os.core.config import settings

            if not settings.DASHSCOPE_API_KEY:
                message_id = str(uuid.uuid4())
                yield sse_event(
                    "message",
                    {
                        "message_id": message_id,
                        "seq": 1,
                        "mode": "full",
                        "role": "assistant",
                        "content": "抱歉，LLM 服务未配置（缺少 DASHSCOPE_API_KEY）。请联系管理员。",
                    },
                )
                yield sse_done(session_id)
                return

            yield sse_event(
                "thinking",
                {"content": "正在思考...", "phase": "plan", "status": "running", "progress": 5},
            )

            # First LLM call (with tools)
            response = await _call_llm(
                api_key=settings.DASHSCOPE_API_KEY,
                model=settings.DASHSCOPE_MODEL,
                messages=llm_messages,
                tools=AGENT_TOOLS,
            )

            if response is None:
                yield sse_error("LLM 调用失败")
                yield sse_done(session_id)
                return

            content = response.get("content", "")
            tool_calls = response.get("tool_calls", [])

            # If LLM wants to call tools
            if tool_calls:
                for tc in tool_calls:
                    func_name = tc["function"]["name"]
                    func_args = json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"]
                    run_id = str(uuid.uuid4())
                    tool_title = _tool_display_name(func_name)

                    yield sse_event(
                        "thinking",
                        {
                            "content": f"正在执行：{tool_title}...",
                            "phase": "tool",
                            "step": func_name,
                            "status": "running",
                            "progress": 10,
                            "run_id": run_id,
                        },
                    )
                    yield sse_event(
                        "tool_start",
                        {
                            "run_id": run_id,
                            "tool": func_name,
                            "title": tool_title,
                            "status": "running",
                            "progress": 0,
                        },
                    )

                    # Execute the corresponding Node
                    node_result = await ctx.execute_node(
                        node_ref=f"builtin://{func_name}",
                        session_id=session_id,
                        tenant_id=tenant.tenant_id,
                        params=func_args,
                    )

                    yield sse_event(
                        "tool_done",
                        {
                            "run_id": run_id,
                            "tool": func_name,
                            "title": tool_title,
                            "status": node_result.status,
                            "progress": 100,
                        },
                    )

                    # Push UI render if node produced ui_schema
                    if node_result.ui_schema:
                        yield sse_event(
                            "ui_render",
                            _enrich_ui_render(
                                node_result.ui_schema,
                                ui_id=ui_default_id,
                                render_mode="replace",
                                schema_version=1,
                                run_id=run_id,
                            ),
                        )
                    elif node_result.result:
                        # Build a basic ui_render from result data
                        ui_data = _result_to_ui(func_name, node_result.result)
                        if ui_data:
                            yield sse_event(
                                "ui_render",
                                _enrich_ui_render(
                                    ui_data,
                                    ui_id=ui_default_id,
                                    render_mode="replace",
                                    schema_version=1,
                                    run_id=run_id,
                                ),
                            )

                    # Feed tool result back to LLM for summary
                    tool_result_text = json.dumps(node_result.result, ensure_ascii=False)[:2000]
                    llm_messages.append({"role": "assistant", "content": "", "tool_calls": [tc]})
                    llm_messages.append({
                        "role": "tool",
                        "content": tool_result_text,
                        "name": func_name,
                    })

                # Second LLM call: summarize tool results
                yield sse_event(
                    "thinking",
                    {
                        "content": "正在整理结果...",
                        "phase": "summarize",
                        "status": "running",
                        "progress": 85,
                    },
                )
                summary_response = await _call_llm(
                    api_key=settings.DASHSCOPE_API_KEY,
                    model=settings.DASHSCOPE_MODEL,
                    messages=llm_messages,
                    tools=None,
                )
                if summary_response and summary_response.get("content"):
                    # Stream the summary text
                    message_id = str(uuid.uuid4())
                    seq = 0
                    for chunk in _chunk_text(summary_response["content"]):
                        seq += 1
                        yield sse_event(
                            "message",
                            {
                                "message_id": message_id,
                                "seq": seq,
                                "mode": "delta",
                                "role": "assistant",
                                "content": chunk,
                            },
                        )

            elif content:
                # No tool calls — pure text response
                message_id = str(uuid.uuid4())
                seq = 0
                for chunk in _chunk_text(content):
                    seq += 1
                    yield sse_event(
                        "message",
                        {
                            "message_id": message_id,
                            "seq": seq,
                            "mode": "delta",
                            "role": "assistant",
                            "content": chunk,
                        },
                    )

            # Done
            yield sse_done(session_id)

        except Exception as exc:
            logger.error("Agent chat error: %s", exc, exc_info=True)
            yield sse_event(
                "error",
                {
                    "code": "INTERNAL_ERROR",
                    "message": f"处理出错: {str(exc)}",
                    "retryable": False,
                },
            )
            yield sse_done(session_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Internal Helpers ─────────────────────────────────────────


def _collect_files(messages: List[UserMessage]) -> List[FileRef]:
    """Collect all file references from messages (typically just the latest)."""
    files: List[FileRef] = []
    for msg in messages:
        if msg.role == "user" and msg.files:
            files.extend(msg.files)
    return files


def _build_llm_messages(
    messages: List[UserMessage],
    file_texts: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """
    Convert frontend messages to LLM-compatible format.

    If file_texts is provided (url -> parsed text), inject the actual
    file content into the message instead of just the URL.
    """
    file_texts = file_texts or {}
    llm_msgs: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]
    for msg in messages:
        entry: Dict[str, Any] = {"role": msg.role, "content": msg.content}
        if msg.files:
            parts: List[str] = []
            for f in msg.files:
                text = file_texts.get(f.url)
                if text:
                    parts.append(f"[附件: {f.name}]\n{text}")
                else:
                    parts.append(f"[附件: {f.name}]（文件处理中或处理失败）")
            entry["content"] = f"{msg.content}\n\n附件内容:\n" + "\n---\n".join(parts)
        llm_msgs.append(entry)
    return llm_msgs


async def _process_files_via_event_bus(
    files: List[FileRef],
    tenant_id: str,
    session_id: str,
    user_id: Optional[str],
    ctx: Any,
    bb: Any,
    timeout: float = 60.0,
) -> Dict[str, str]:
    """
    Publish FILE_UPLOADED events and wait for FILE_READY responses.

    Flow:
      1. For each file, publish FILE_UPLOADED to EventBus with OSS URL.
      2. Tonglu's EventSinkListener picks up the event, pulls from OSS,
         parses the file, and publishes FILE_READY with text content.
      3. This function subscribes and waits for all FILE_READY events.

    Returns:
        Dict mapping file URL to parsed text content.
    """
    if not files:
        return {}

    result: Dict[str, str] = {}
    pending_urls = {f.url for f in files}
    ready_event = asyncio.Event()

    bus = ctx.get_bus(tenant_id)

    async def _on_file_ready(event: TempoEvent) -> None:
        """Handler for FILE_READY events."""
        if event.session_id != session_id:
            return
        url = event.payload.get("file_url", "")
        text = event.payload.get("text_content", "")
        if url in pending_urls:
            result[url] = text
            pending_urls.discard(url)
            if not pending_urls:
                ready_event.set()

    # Subscribe to FILE_READY events
    pubsub = await bus.subscribe(_on_file_ready, event_filter=FILE_READY)

    try:
        # Publish FILE_UPLOADED for each file
        for f in files:
            file_id = str(uuid.uuid4())
            # Also store file ref in Blackboard so Tonglu can find it
            await bb.set_state(session_id, f"_file:{file_id}", {
                "url": f.url, "name": f.name, "type": f.type,
            })

            event = TempoEvent.create(
                type=FILE_UPLOADED,
                source="agent_controller",
                tenant_id=tenant_id,
                session_id=session_id,
                payload={
                    "file_id": file_id,
                    "file_url": f.url,
                    "file_name": f.name,
                    "file_type": f.type,
                    "user_id": user_id or "",
                },
            )
            await bus.publish(event)
            logger.info("Published FILE_UPLOADED: file=%s url=%s", f.name, f.url)

        # Wait for all FILE_READY responses (with timeout)
        try:
            await asyncio.wait_for(ready_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Timeout waiting for FILE_READY: got %d/%d files",
                len(result), len(files),
            )
            # Fill missing files with timeout message
            for f in files:
                if f.url not in result:
                    result[f.url] = f"（文件 {f.name} 处理超时，请稍后重试）"

    finally:
        # Cleanup subscription
        await pubsub.unsubscribe()
        await pubsub.aclose()

    return result


_LLM_MAX_RETRIES = 3


async def _call_llm(
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict]] = None,
    enable_search: bool = False,
    search_options: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Call DashScope Generation API with retry on transient failures.

    Retries up to _LLM_MAX_RETRIES times with exponential backoff (1s, 2s, 4s)
    for network errors (SSL, timeout, connection reset).

    Returns dict with 'content', optional 'tool_calls', and optional 'search_results'.
    """

    def _sync_call() -> Dict[str, Any]:
        import dashscope

        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "api_key": api_key,
            "result_format": "message",
        }
        if tools:
            kwargs["tools"] = tools
        if enable_search:
            kwargs["enable_search"] = True
            if search_options:
                kwargs["search_options"] = search_options

        response = dashscope.Generation.call(**kwargs)
        if response.status_code != 200:
            raise RuntimeError(
                f"DashScope error: {response.code} - {response.message}"
            )

        choice = response.output.choices[0].message
        result: Dict[str, Any] = {
            "content": getattr(choice, "content", "") or "",
            "tool_calls": [],
        }
        if hasattr(choice, "tool_calls") and choice.tool_calls:
            result["tool_calls"] = [
                {
                    "id": getattr(tc, "id", str(uuid.uuid4())),
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in choice.tool_calls
            ]

        search_info = getattr(response.output, "search_info", None)
        if search_info and hasattr(search_info, "search_results"):
            result["search_results"] = [
                {
                    "title": getattr(web, "title", ""),
                    "url": getattr(web, "url", ""),
                    "index": getattr(web, "index", ""),
                }
                for web in search_info.search_results
            ]

        return result

    last_error: Optional[Exception] = None
    for attempt in range(_LLM_MAX_RETRIES):
        try:
            return await asyncio.to_thread(_sync_call)
        except Exception as e:
            last_error = e
            if attempt < _LLM_MAX_RETRIES - 1:
                wait = 2 ** attempt
                logger.warning(
                    "LLM call failed (attempt %d/%d, model=%s): %s. Retrying in %ds...",
                    attempt + 1, _LLM_MAX_RETRIES, model, e, wait,
                )
                await asyncio.sleep(wait)

    logger.error("LLM call failed after %d attempts: %s", _LLM_MAX_RETRIES, last_error)
    return None


def _chunk_text(text: str, chunk_size: int = 4) -> List[str]:
    """Split text into small chunks for simulated streaming effect."""
    if not text:
        return []
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]


def _tool_display_name(tool_name: str) -> str:
    """Human-readable tool name for thinking status."""
    names = {
        "search": "联网搜索",
        "writer": "智能撰写",
        "data_query": "数据检索",
    }
    return names.get(tool_name, tool_name)


def _enrich_ui_render(
    ui_schema: Dict[str, Any],
    *,
    ui_id: str,
    render_mode: str,
    schema_version: int,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Ensure ui_render payload contains animation-friendly meta fields.

    We preserve existing keys and only add missing fields.
    """
    if not isinstance(ui_schema, dict):
        return {
            "schema_version": schema_version,
            "ui_id": ui_id,
            "render_mode": render_mode,
            "component": "raw_json",
            "title": "UI Schema",
            "data": {"raw": ui_schema},
        }

    enriched = dict(ui_schema)
    enriched.setdefault("schema_version", schema_version)
    enriched.setdefault("ui_id", ui_id)
    enriched.setdefault("render_mode", render_mode)
    if run_id is not None:
        enriched.setdefault("run_id", run_id)
    return enriched


def _result_to_ui(tool_name: str, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Build a basic ui_render payload from node result when ui_schema is absent.

    This is a fallback — nodes should ideally provide their own ui_schema.
    """
    if not result:
        return None

    # If result already has a 'type' field (our output schema convention)
    result_type = result.get("type")
    if result_type == "table":
        return {
            "component": "smart_table",
            "title": result.get("title", "查询结果"),
            "data": {
                "columns": result.get("columns", []),
                "rows": result.get("rows", []),
            },
            "actions": [
                {"label": "导出 Excel", "action_type": "download_json_as_xlsx"},
            ],
        }
    elif result_type in ("document_fill", "document"):
        return {
            "component": "document_preview",
            "title": result.get("title", "文档预览"),
            "data": result,
            "actions": [
                {"label": "下载 Word", "action_type": "download_generated_file"},
            ],
        }
    elif result_type == "report":
        return {
            "component": "chart_report",
            "title": result.get("title", "报表"),
            "data": result,
        }

    # Generic fallback: show as JSON
    return {
        "component": "smart_table",
        "title": "执行结果",
        "data": {"columns": [], "rows": [], "raw": result},
    }
