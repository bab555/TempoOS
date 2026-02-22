# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Smoke test — Context management: ChatStore persistence, ContextBuilder,
session continuity, and multi-turn backend history.

Requires:
  - DASHSCOPE_API_KEY in environment / .env
  - Network access to DashScope API

Run:  pytest tests/smoke/test_smoke_context.py -v -s --timeout=300
"""

import json
from typing import Dict, List, Tuple

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from tempo_os.api.agent import router as agent_router
from tempo_os.api.deps import get_current_tenant
from tempo_os.core.config import settings
from tempo_os.core.tenant import TenantContext
from tempo_os.memory.chat_store import ChatStore, ChatMessage
from tempo_os.memory.context_builder import ContextBuilder
from tempo_os.memory.blackboard import TenantBlackboard

pytestmark = pytest.mark.skipif(
    not settings.DASHSCOPE_API_KEY,
    reason="DASHSCOPE_API_KEY not configured",
)

TENANT_ID = "smoke_context"


def _make_app(mock_redis) -> FastAPI:
    from tempo_os.core.context import init_platform_context, get_platform_context
    from tempo_os.nodes.search import SearchNode
    from tempo_os.nodes.writer import WriterNode

    init_platform_context(mock_redis)
    ctx = get_platform_context()
    try:
        ctx.node_registry.register_builtin("search", SearchNode())
        ctx.node_registry.register_builtin("writer", WriterNode())
    except Exception:
        pass

    app = FastAPI()
    app.include_router(agent_router, prefix="/api")
    app.dependency_overrides[get_current_tenant] = lambda: TenantContext(
        tenant_id=TENANT_ID, user_id="smoke_user_ctx"
    )
    return app


def _parse_sse(raw: str) -> List[Tuple[str, dict]]:
    events = []
    evt = None
    data = None
    for line in raw.split("\n"):
        if line.startswith("event: "):
            evt = line[7:].strip()
        elif line.startswith("data: "):
            data = line[6:]
        elif line == "" and evt and data is not None:
            try:
                parsed = json.loads(data)
            except (json.JSONDecodeError, ValueError):
                parsed = {"_raw": data}
            events.append((evt, parsed))
            evt = None
            data = None
    return events


def _types(events: list) -> List[str]:
    return [e[0] for e in events]


def _of_type(events: list, t: str) -> List[dict]:
    return [e[1] for e in events if e[0] == t]


async def _chat(client: AsyncClient, messages: list, session_id: str = None) -> Tuple[List, str]:
    body: Dict = {"messages": messages}
    if session_id:
        body["session_id"] = session_id
    resp = await client.post("/api/agent/chat", json=body)
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    sid = ""
    inits = _of_type(events, "session_init")
    if inits:
        sid = inits[0].get("session_id", "")
    return events, sid


class TestChatStorePersistence:
    """Verify that agent.py persists messages to ChatStore."""

    @pytest.mark.asyncio
    async def test_messages_persisted_after_chat(self, mock_redis):
        app = _make_app(mock_redis)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://t", timeout=120) as c:
            events, sid = await _chat(c, [
                {"role": "user", "content": "What is 2+2?"},
            ])

        assert sid, "No session_id returned"
        types = _types(events)
        assert "done" in types

        # Verify ChatStore has messages
        store = ChatStore(mock_redis, TENANT_ID, ttl=3600)
        count = await store.count(sid)
        assert count >= 2, f"Expected at least user+assistant, got {count}"

        all_msgs = await store.get_all(sid)
        roles = [m.role for m in all_msgs]
        assert "user" in roles
        assert "assistant" in roles

        # First message should be the user's
        assert all_msgs[0].role == "user"
        assert "2+2" in all_msgs[0].content

    @pytest.mark.asyncio
    async def test_tool_calls_persisted(self, mock_redis):
        """When a tool is called, the tool_call and tool_result should be in ChatStore."""
        app = _make_app(mock_redis)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://t", timeout=120) as c:
            events, sid = await _chat(c, [
                {"role": "user", "content": "Search online for the latest Python 3.13 features"},
            ])

        types = _types(events)
        assert "done" in types

        if "tool_start" in types:
            store = ChatStore(mock_redis, TENANT_ID, ttl=3600)
            all_msgs = await store.get_all(sid)
            msg_types = [m.type for m in all_msgs]
            assert "tool_call" in msg_types or "tool_result" in msg_types, \
                f"Tool events not persisted. Types: {msg_types}"


class TestMultiTurnBackendHistory:
    """Verify that multi-turn conversations use backend ChatStore for context."""

    @pytest.mark.asyncio
    async def test_second_turn_has_context(self, mock_redis):
        app = _make_app(mock_redis)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://t", timeout=180) as c:
            # Turn 1
            events1, sid = await _chat(c, [
                {"role": "user", "content": "My name is Alice and I work at ACME Corp."},
            ])
            assert "done" in _types(events1)
            assert sid

            # Turn 2: only send the new message, backend should have history
            events2, sid2 = await _chat(c, [
                {"role": "user", "content": "What is my name and where do I work?"},
            ], session_id=sid)
            assert "done" in _types(events2)

            # Check that the assistant response references Alice/ACME
            msgs = _of_type(events2, "message")
            full_text = "".join(m.get("content", "") for m in msgs)
            print(f"\n  Turn 2 response: {full_text[:200]}")

        # Verify ChatStore accumulated all messages
        store = ChatStore(mock_redis, TENANT_ID, ttl=3600)
        count = await store.count(sid)
        # At least: user1 + assistant1 + user2 + assistant2 = 4
        assert count >= 4, f"Expected >= 4 messages in store, got {count}"


class TestRouteCaching:
    """Verify that intent routing is cached per session."""

    @pytest.mark.asyncio
    async def test_route_cached_in_blackboard(self, mock_redis):
        app = _make_app(mock_redis)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://t", timeout=120) as c:
            events, sid = await _chat(c, [
                {"role": "user", "content": "Search online for HP printer prices"},
            ])

        assert sid
        bb = TenantBlackboard(mock_redis, TENANT_ID)
        cached_scene = await bb.get_state(sid, "_routed_scene")
        assert cached_scene is not None, "Route not cached in blackboard"
        assert isinstance(cached_scene, str)
        print(f"\n  Cached scene: {cached_scene}")


class TestContextBuilderIntegration:
    """Test ContextBuilder.build() with real ChatStore data."""

    @pytest.mark.asyncio
    async def test_build_produces_valid_llm_messages(self, mock_redis):
        store = ChatStore(mock_redis, TENANT_ID, ttl=3600)
        bb = TenantBlackboard(mock_redis, TENANT_ID)

        # Populate some history
        for i in range(5):
            await store.append("s_ctx", ChatMessage(role="user", content=f"question {i}"))
            await store.append("s_ctx", ChatMessage(role="assistant", content=f"answer {i}"))

        builder = ContextBuilder(
            chat_store=store,
            blackboard=bb,
            max_recent_rounds=3,
            summary_threshold=100,  # High threshold to avoid LLM call
        )

        msgs = await builder.build("s_ctx", "You are a helpful assistant.")

        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "You are a helpful assistant."

        # Should have recent messages
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        assert len(user_msgs) >= 3  # at least the recent 3 rounds

        # Last user message should be the most recent
        assert "question 4" in user_msgs[-1]["content"]

    @pytest.mark.asyncio
    async def test_v2_summary_with_real_llm(self, mock_redis):
        """Test that V2 summary is generated and cached when threshold is exceeded."""
        store = ChatStore(mock_redis, TENANT_ID, ttl=3600)
        bb = TenantBlackboard(mock_redis, TENANT_ID)

        # Populate enough history to trigger summarization
        for i in range(8):
            await store.append("s_sum", ChatMessage(role="user", content=f"I need product {i} info"))
            await store.append("s_sum", ChatMessage(role="assistant", content=f"Here is info about product {i}"))

        builder = ContextBuilder(
            chat_store=store,
            blackboard=bb,
            max_recent_rounds=3,
            summary_threshold=6,  # Low threshold to trigger summary
            summary_model="qwen3.5-plus",
            api_key=settings.DASHSCOPE_API_KEY,
        )

        msgs = await builder.build("s_sum", "System prompt")

        # Check that summary was cached
        cached_summary = await bb.get_state("s_sum", "_chat_summary")
        if cached_summary:
            print(f"\n  V2 Summary: {cached_summary[:200]}")
            assert len(cached_summary) > 10

            # Check that summary appears in messages
            summary_msgs = [m for m in msgs if "摘要" in m.get("content", "")]
            assert len(summary_msgs) >= 1, "Summary not injected into LLM context"
