# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Agent Controller — Central LLM-driven chat endpoint with SSE streaming.

This is the primary entry point for frontend interaction. The LLM acts as
a central controller that decides whether to:
  - Reply with plain text (left-side chat bubble)
  - Invoke a tool/node (backend execution, then push UI result)
  - Request more information from the user

Key architectural features:
  1. **Prompt Routing** — A lightweight LLM call classifies user intent
     into a scene (procurement, document_writing, data_analysis, general),
     then loads the corresponding system prompt and tool set from .md files.
     Routing results are cached per-session to avoid redundant LLM calls.
  2. **ReAct Loop** — The LLM can call tools multiple times in sequence
     (e.g. search → data_query → writer) without returning to the user
     between steps. The loop continues until the LLM produces a final
     text response with no tool calls, or a safety limit is reached.
  3. **Backend Chat History** — All messages are persisted in ChatStore
     (Redis List). The ContextBuilder reads from ChatStore to construct
     LLM context with V1 trim and V2 LLM summary for long conversations.

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
from tempo_os.agents.prompt_loader import (
    get_scene_config,
    route_intent,
    DEFAULT_SCENE,
)
from tempo_os.memory.chat_store import ChatMessage, ChatStore
from tempo_os.memory.context_builder import ContextBuilder

logger = logging.getLogger("tempo.api.agent")

router = APIRouter(prefix="/agent", tags=["agent"])

MAX_REACT_ROUNDS = 8


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


# ── Endpoint ─────────────────────────────────────────────────


@router.post("/chat")
async def agent_chat(
    req: AgentChatRequest,
    request: Request,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """
    Central Agent chat endpoint with SSE streaming.

    Flow:
      1. Persist user message to ChatStore
      2. Route user intent → scene_key (cached per session)
      3. Load scene prompt + tools
      4. Build LLM context via ContextBuilder (V1 trim / V2 summary)
      5. Pre-process uploaded files
      6. Enter ReAct loop (LLM → tool calls → observe → repeat)
      7. Persist assistant response to ChatStore
      8. Stream final text response
    """
    session_id = req.session_id or str(uuid.uuid4())

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            ui_default_id = "panel_main"
            yield sse_event("session_init", {"session_id": session_id})

            ctx = get_platform_context()
            bb = ctx.get_blackboard(tenant.tenant_id)

            await bb.set_state(session_id, "_tenant_id", tenant.tenant_id)
            if tenant.user_id:
                await bb.set_state(session_id, "_user_id", tenant.user_id)

            from tempo_os.core.config import settings

            if not settings.DASHSCOPE_API_KEY:
                message_id = str(uuid.uuid4())
                yield sse_event(
                    "message",
                    {
                        "message_id": message_id, "seq": 1, "mode": "full",
                        "role": "assistant",
                        "content": "抱歉，LLM 服务未配置（缺少 DASHSCOPE_API_KEY）。请联系管理员。",
                    },
                )
                yield sse_done(session_id)
                return

            # ── Initialize ChatStore & ContextBuilder ─────────
            chat_store = ChatStore(
                redis=ctx.redis,
                tenant_id=tenant.tenant_id,
                ttl=settings.CHAT_HISTORY_TTL,
            )
            context_builder = ContextBuilder(
                chat_store=chat_store,
                blackboard=bb,
                max_recent_rounds=settings.LLM_CONTEXT_MAX_ROUNDS,
                summary_threshold=settings.LLM_CONTEXT_SUMMARY_THRESHOLD,
                summary_model=settings.DASHSCOPE_SUMMARY_MODEL,
                api_key=settings.DASHSCOPE_API_KEY,
            )

            # ── Step 0.5: Restore from PG if session expired ──
            if req.session_id:
                existing_count = await chat_store.count(session_id)
                if existing_count == 0:
                    await _try_restore_session(
                        tenant_id=tenant.tenant_id,
                        session_id=session_id,
                        tonglu_base_url=settings.TONGLU_BASE_URL,
                        session_ttl=settings.SESSION_TTL,
                        chat_ttl=settings.CHAT_HISTORY_TTL,
                    )

            # ── Step 1: Persist user message ──────────────────
            latest_user_msg = _get_latest_user_message(req.messages)
            all_files = _collect_files(req.messages)

            user_chat_msg = ChatMessage(
                role="user",
                content=latest_user_msg,
                files=[{"name": f.name, "url": f.url, "type": f.type} for f in all_files] if all_files else None,
            )
            await chat_store.append(session_id, user_chat_msg)

            # ── Step 2: Route intent (with session cache) ─────
            yield sse_event(
                "thinking",
                {"content": "正在分析意图...", "phase": "route", "status": "running", "progress": 2},
            )

            scene_key = await _route_with_cache(
                bb=bb,
                session_id=session_id,
                user_message=latest_user_msg,
                api_key=settings.DASHSCOPE_API_KEY,
                model=settings.DASHSCOPE_MODEL,
            )

            system_prompt, scene_tools = get_scene_config(scene_key)

            yield sse_event(
                "thinking",
                {"content": "正在思考...", "phase": "plan", "status": "running", "progress": 5,
                 "scene": scene_key},
            )

            # ── Step 3: Pre-process files ─────────────────────
            file_texts: Dict[str, str] = {}

            if all_files:
                yield sse_event(
                    "thinking",
                    {"content": "正在处理上传文件...", "phase": "file_processing",
                     "status": "running", "progress": 3},
                )
                file_texts = await _process_files_via_event_bus(
                    files=all_files,
                    tenant_id=tenant.tenant_id,
                    session_id=session_id,
                    user_id=tenant.user_id,
                    ctx=ctx,
                    bb=bb,
                )

            # ── Step 4: Build LLM context via ContextBuilder ──
            llm_messages = await context_builder.build(session_id, system_prompt)

            # Inject file texts into the latest user message if present
            if file_texts:
                _inject_file_texts(llm_messages, file_texts, all_files)

            # ── Step 5: ReAct Loop ────────────────────────────
            total_tool_calls = 0
            assistant_content = ""

            for react_round in range(MAX_REACT_ROUNDS):
                response = await _call_llm(
                    api_key=settings.DASHSCOPE_API_KEY,
                    model=settings.DASHSCOPE_MODEL,
                    messages=llm_messages,
                    tools=scene_tools,
                )

                if response is None:
                    yield sse_error("LLM 调用失败")
                    yield sse_done(session_id)
                    return

                content = response.get("content", "")
                tool_calls = response.get("tool_calls", [])

                if not tool_calls:
                    assistant_content = content or ""
                    if assistant_content:
                        message_id = str(uuid.uuid4())
                        seq = 0
                        for chunk in _chunk_text(assistant_content):
                            seq += 1
                            yield sse_event(
                                "message",
                                {
                                    "message_id": message_id, "seq": seq,
                                    "mode": "delta", "role": "assistant",
                                    "content": chunk,
                                },
                            )
                    # Persist immediately before break
                    await chat_store.append(session_id, ChatMessage(
                        role="assistant",
                        content=assistant_content or "(completed)",
                    ))
                    break

                for tc in tool_calls:
                    total_tool_calls += 1
                    func_name = tc["function"]["name"]
                    func_args = (
                        json.loads(tc["function"]["arguments"])
                        if isinstance(tc["function"]["arguments"], str)
                        else tc["function"]["arguments"]
                    )
                    run_id = str(uuid.uuid4())
                    tool_title = _tool_display_name(func_name)

                    progress_base = min(10 + total_tool_calls * 15, 80)

                    yield sse_event(
                        "thinking",
                        {
                            "content": f"正在执行：{tool_title}...",
                            "phase": "tool", "step": func_name,
                            "status": "running", "progress": progress_base,
                            "run_id": run_id, "round": react_round + 1,
                        },
                    )
                    yield sse_event(
                        "tool_start",
                        {
                            "run_id": run_id, "tool": func_name,
                            "title": tool_title, "status": "running",
                            "progress": 0,
                        },
                    )

                    node_result = await ctx.execute_node(
                        node_ref=f"builtin://{func_name}",
                        session_id=session_id,
                        tenant_id=tenant.tenant_id,
                        params=func_args,
                    )

                    yield sse_event(
                        "tool_done",
                        {
                            "run_id": run_id, "tool": func_name,
                            "title": tool_title, "status": node_result.status,
                            "progress": 100,
                        },
                    )

                    if node_result.ui_schema:
                        yield sse_event(
                            "ui_render",
                            _enrich_ui_render(
                                node_result.ui_schema,
                                ui_id=ui_default_id, render_mode="replace",
                                schema_version=1, run_id=run_id,
                            ),
                        )
                    elif node_result.result:
                        ui_data = _result_to_ui(func_name, node_result.result)
                        if ui_data:
                            yield sse_event(
                                "ui_render",
                                _enrich_ui_render(
                                    ui_data,
                                    ui_id=ui_default_id, render_mode="replace",
                                    schema_version=1, run_id=run_id,
                                ),
                            )

                    tool_result_text = json.dumps(
                        node_result.result, ensure_ascii=False,
                    )[:4000]
                    llm_messages.append({
                        "role": "assistant", "content": content or "",
                        "tool_calls": [tc],
                    })
                    llm_messages.append({
                        "role": "tool",
                        "content": tool_result_text,
                        "name": func_name,
                    })

                    # Persist tool interaction to ChatStore
                    await chat_store.append_batch(session_id, [
                        ChatMessage(
                            role="assistant",
                            content=content or f"[调用工具: {func_name}]",
                            msg_type="tool_call",
                            tool_name=func_name,
                        ),
                        ChatMessage(
                            role="tool",
                            content=tool_result_text,
                            msg_type="tool_result",
                            tool_name=func_name,
                            tool_call_id=tc.get("id"),
                        ),
                    ])

                yield sse_event(
                    "thinking",
                    {
                        "content": "正在继续思考...",
                        "phase": "react", "status": "running",
                        "progress": min(progress_base + 10, 90),
                        "round": react_round + 2,
                    },
                )

            else:
                assistant_content = "已完成所有工具调用，以上是执行结果。如需进一步操作请继续对话。"
                logger.warning(
                    "ReAct loop hit max rounds (%d) for session %s",
                    MAX_REACT_ROUNDS, session_id,
                )
                yield sse_event(
                    "message",
                    {
                        "message_id": str(uuid.uuid4()), "seq": 1,
                        "mode": "full", "role": "assistant",
                        "content": assistant_content,
                    },
                )

            # ── Step 6: Persist assistant response ────────────
            final_text = assistant_content or ""
            if final_text:
                await chat_store.append(session_id, ChatMessage(
                    role="assistant",
                    content=final_text,
                ))
            else:
                # LLM returned empty content (edge case) — still persist
                # a placeholder so multi-turn history stays consistent
                await chat_store.append(session_id, ChatMessage(
                    role="assistant",
                    content="（已完成处理）",
                ))

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


async def _try_restore_session(
    tenant_id: str,
    session_id: str,
    tonglu_base_url: str,
    session_ttl: int = 1800,
    chat_ttl: int = 86400,
) -> bool:
    """
    Attempt to restore an expired session from Tonglu's PG snapshot.

    Called when a request arrives with a session_id but ChatStore is empty
    (session data expired from Redis). Makes an HTTP call to Tonglu's
    /session/restore endpoint which reads the PG snapshot and writes
    it back to Redis.

    Returns True if restore succeeded, False otherwise.
    """
    import httpx

    url = f"{tonglu_base_url.rstrip('/')}/session/restore"
    payload = {
        "tenant_id": tenant_id,
        "session_id": session_id,
        "session_ttl": session_ttl,
        "chat_ttl": chat_ttl,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("restored"):
                    logger.info(
                        "Session restored from PG: tenant=%s session=%s",
                        tenant_id, session_id,
                    )
                    return True
                else:
                    logger.debug(
                        "No PG snapshot for session: tenant=%s session=%s",
                        tenant_id, session_id,
                    )
            else:
                logger.warning(
                    "Tonglu restore call failed: status=%d body=%s",
                    resp.status_code, resp.text[:200],
                )
    except Exception as e:
        logger.warning("Tonglu restore call error: %s", e)

    return False


def _get_latest_user_message(messages: List[UserMessage]) -> str:
    """Extract the text of the latest user message for routing."""
    for msg in reversed(messages):
        if msg.role == "user" and msg.content.strip():
            return msg.content.strip()
    return ""


def _collect_files(messages: List[UserMessage]) -> List[FileRef]:
    """Collect all file references from messages."""
    files: List[FileRef] = []
    for msg in messages:
        if msg.role == "user" and msg.files:
            files.extend(msg.files)
    return files


async def _route_with_cache(
    bb: Any,
    session_id: str,
    user_message: str,
    api_key: str,
    model: str,
) -> str:
    """
    Route intent with session-level caching.

    On the first message of a session, perform LLM routing and cache the
    result. Subsequent messages reuse the cached scene unless the user's
    intent clearly shifts (detected by keyword heuristic).
    """
    cached_scene = await bb.get_state(session_id, "_routed_scene")
    if cached_scene and isinstance(cached_scene, str) and cached_scene != DEFAULT_SCENE:
        return cached_scene

    scene_key = await route_intent(
        user_message=user_message,
        api_key=api_key,
        model=model,
    )
    await bb.set_state(session_id, "_routed_scene", scene_key)
    return scene_key


def _inject_file_texts(
    llm_messages: List[Dict[str, Any]],
    file_texts: Dict[str, str],
    files: List[FileRef],
) -> None:
    """Append file content to the last user message in llm_messages."""
    for i in range(len(llm_messages) - 1, -1, -1):
        if llm_messages[i].get("role") == "user":
            parts: List[str] = []
            for f in files:
                text = file_texts.get(f.url)
                if text:
                    parts.append(f"[附件: {f.name}]\n{text}")
                else:
                    parts.append(f"[附件: {f.name}]（文件处理中或处理失败）")
            if parts:
                llm_messages[i]["content"] += "\n\n附件内容:\n" + "\n---\n".join(parts)
            break


def _build_llm_messages(
    messages: List[UserMessage],
    system_prompt: str,
    file_texts: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """
    Convert frontend messages to LLM-compatible format.

    Kept for backward compatibility; the primary path now uses
    ContextBuilder.build() which reads from ChatStore.
    """
    file_texts = file_texts or {}
    llm_msgs: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
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
    """
    if not files:
        return {}

    result: Dict[str, str] = {}
    pending_urls = {f.url for f in files}
    ready_event = asyncio.Event()

    bus = ctx.get_bus(tenant_id)

    async def _on_file_ready(event: TempoEvent) -> None:
        if event.session_id != session_id:
            return
        url = event.payload.get("file_url", "")
        text = event.payload.get("text_content", "")
        if url in pending_urls:
            result[url] = text
            pending_urls.discard(url)
            if not pending_urls:
                ready_event.set()

    pubsub = await bus.subscribe(_on_file_ready, event_filter=FILE_READY)

    try:
        for f in files:
            file_id = str(uuid.uuid4())
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

        try:
            await asyncio.wait_for(ready_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Timeout waiting for FILE_READY: got %d/%d files",
                len(result), len(files),
            )
            for f in files:
                if f.url not in result:
                    result[f.url] = f"（文件 {f.name} 处理超时，请稍后重试）"

    finally:
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

    Retries up to _LLM_MAX_RETRIES times with exponential backoff.
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

        def _get(obj, key, default=""):
            """Access a field from either dict or object."""
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        result: Dict[str, Any] = {
            "content": _get(choice, "content", "") or "",
            "tool_calls": [],
        }

        raw_tool_calls = _get(choice, "tool_calls", None)
        if raw_tool_calls:
            parsed_calls = []
            for tc in raw_tool_calls:
                func = _get(tc, "function", {})
                parsed_calls.append({
                    "id": _get(tc, "id", str(uuid.uuid4())),
                    "type": "function",
                    "function": {
                        "name": _get(func, "name", ""),
                        "arguments": _get(func, "arguments", "{}"),
                    },
                })
            result["tool_calls"] = parsed_calls

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
    """Ensure ui_render payload contains animation-friendly meta fields."""
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
    """Build a basic ui_render payload from node result when ui_schema is absent."""
    if not result:
        return None

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

    return {
        "component": "smart_table",
        "title": "执行结果",
        "data": {"columns": [], "rows": [], "raw": result},
    }
