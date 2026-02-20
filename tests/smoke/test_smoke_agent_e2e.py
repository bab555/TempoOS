# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Smoke test -- End-to-end Agent chat via SSE.
Covers: greeting, search, writer, multi-turn, full SSE event validation.

Requires:
  - DASHSCOPE_API_KEY in environment / .env
  - Network access to DashScope API

Run:  pytest tests/smoke/test_smoke_agent_e2e.py -v -s --timeout=300
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

pytestmark = pytest.mark.skipif(
    not settings.DASHSCOPE_API_KEY,
    reason="DASHSCOPE_API_KEY not configured",
)


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
        tenant_id="smoke_e2e", user_id="smoke_user_001"
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
    assert "text/event-stream" in resp.headers.get("content-type", "")
    events = _parse_sse(resp.text)
    sid = ""
    inits = _of_type(events, "session_init")
    if inits:
        sid = inits[0].get("session_id", "")
    return events, sid


class TestSSEEventStructure:
    """Validate SSE event types and payload fields."""

    @pytest.mark.asyncio
    async def test_greeting_event_sequence(self, mock_redis):
        app = _make_app(mock_redis)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t", timeout=120) as c:
            events, sid = await _chat(c, [{"role": "user", "content": "Hello, introduce yourself"}])

        types = _types(events)
        print(f"\n--- Greeting events: {types} ---")

        assert "session_init" in types, "Missing session_init"
        assert "done" in types, "Missing done"
        assert "message" in types or "error" in types

        # session_init payload
        init = _of_type(events, "session_init")[0]
        assert "session_id" in init
        assert len(init["session_id"]) > 8

        # done payload
        done = _of_type(events, "done")[0]
        assert "session_id" in done
        assert done["session_id"] == init["session_id"]

        # message payloads have required fields
        msgs = _of_type(events, "message")
        if msgs:
            m = msgs[0]
            assert "message_id" in m
            assert "seq" in m
            assert "mode" in m
            assert m["mode"] in ("delta", "full")
            assert "content" in m
            assert "role" in m
            print(f"  First message chunk: {m['content'][:60]}")

    @pytest.mark.asyncio
    async def test_thinking_events_have_phase(self, mock_redis):
        app = _make_app(mock_redis)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t", timeout=120) as c:
            events, _ = await _chat(c, [
                {"role": "user", "content": "Search online for A4 paper prices and compare 3 brands"},
            ])

        thinkings = _of_type(events, "thinking")
        print(f"\n--- Thinking events: {len(thinkings)} ---")
        for t in thinkings:
            print(f"  phase={t.get('phase')}, status={t.get('status')}, progress={t.get('progress')}")
            assert "content" in t
            assert "phase" in t
            assert "status" in t


class TestSearchE2E:
    @pytest.mark.asyncio
    async def test_search_produces_tool_events(self, mock_redis):
        app = _make_app(mock_redis)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t", timeout=120) as c:
            events, sid = await _chat(c, [
                {"role": "user", "content": "Search online and compare prices for HP LaserJet printers, list top 3"},
            ])

        types = _types(events)
        print(f"\n--- Search E2E events: {types} ---")

        assert "done" in types

        if "tool_start" in types:
            ts = _of_type(events, "tool_start")
            for t in ts:
                assert "run_id" in t
                assert "tool" in t
                assert "status" in t
                assert t["status"] == "running"
                print(f"  tool_start: tool={t['tool']}, run_id={t['run_id'][:8]}...")

            td = _of_type(events, "tool_done")
            assert len(td) >= 1
            for t in td:
                assert "run_id" in t
                assert "status" in t
                assert t["progress"] == 100
                print(f"  tool_done: tool={t['tool']}, status={t['status']}")

            # If search succeeded, should have ui_render
            if "ui_render" in types:
                uis = _of_type(events, "ui_render")
                for u in uis:
                    assert "component" in u
                    assert "ui_id" in u
                    assert "render_mode" in u
                    assert "schema_version" in u
                    print(f"  ui_render: component={u['component']}, ui_id={u['ui_id']}")


class TestWriterE2E:
    @pytest.mark.asyncio
    async def test_writer_produces_ui(self, mock_redis):
        app = _make_app(mock_redis)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t", timeout=120) as c:
            events, sid = await _chat(c, [
                {"role": "user", "content": (
                    "Please generate a quotation for the following items:\n"
                    "- ThinkPad X1 Carbon x5, unit price 9999\n"
                    "- Dell 27in Monitor x5, unit price 3999\n"
                    "Client: CSCEC 4th Bureau"
                )},
            ])

        types = _types(events)
        print(f"\n--- Writer E2E events: {types} ---")

        assert "done" in types

        if "tool_start" in types:
            ts = _of_type(events, "tool_start")
            writer_calls = [t for t in ts if t.get("tool") == "writer"]
            if writer_calls:
                print(f"  Writer tool called: {len(writer_calls)} time(s)")

            if "ui_render" in types:
                uis = _of_type(events, "ui_render")
                for u in uis:
                    print(f"  ui_render: component={u['component']}, title={u.get('title', 'N/A')}")


class TestMultiTurnConversation:
    """Simulate a real multi-turn workflow: search -> quotation -> contract."""

    @pytest.mark.asyncio
    async def test_multi_turn_chain(self, mock_redis):
        app = _make_app(mock_redis)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://t", timeout=180) as c:

            # Turn 1: Search
            print("\n" + "=" * 50)
            print("TURN 1: Search")
            print("=" * 50)
            events1, sid = await _chat(c, [
                {"role": "user", "content": "Search online for ThinkPad X1 Carbon laptop prices"},
            ])
            types1 = _types(events1)
            print(f"  Events: {types1}")
            assert "done" in types1
            assert sid, "No session_id returned"

            # Collect assistant reply for conversation context
            msgs1 = _of_type(events1, "message")
            assistant_text_1 = "".join(m.get("content", "") for m in msgs1)

            # Turn 2: Generate quotation (same session)
            print("\n" + "=" * 50)
            print("TURN 2: Quotation")
            print("=" * 50)
            events2, sid2 = await _chat(c, [
                {"role": "user", "content": "Search online for ThinkPad X1 Carbon laptop prices"},
                {"role": "assistant", "content": assistant_text_1[:500] if assistant_text_1 else "Search completed."},
                {"role": "user", "content": (
                    "Based on the search results, generate a quotation:\n"
                    "- ThinkPad X1 Carbon x10\n"
                    "Client: CSCEC 4th Bureau"
                )},
            ], session_id=sid)
            types2 = _types(events2)
            print(f"  Events: {types2}")
            assert "done" in types2

            msgs2 = _of_type(events2, "message")
            assistant_text_2 = "".join(m.get("content", "") for m in msgs2)

            # Turn 3: Generate contract (same session)
            print("\n" + "=" * 50)
            print("TURN 3: Contract")
            print("=" * 50)
            events3, sid3 = await _chat(c, [
                {"role": "user", "content": "Search ThinkPad X1 Carbon prices"},
                {"role": "assistant", "content": assistant_text_1[:200] if assistant_text_1 else "Done."},
                {"role": "user", "content": "Generate quotation for 10 units"},
                {"role": "assistant", "content": assistant_text_2[:200] if assistant_text_2 else "Done."},
                {"role": "user", "content": (
                    "Based on the quotation, generate a procurement contract.\n"
                    "Party A: Shenzhen Digital Tech Co.\n"
                    "Party B: CSCEC 4th Bureau"
                )},
            ], session_id=sid)
            types3 = _types(events3)
            print(f"  Events: {types3}")
            assert "done" in types3

        print("\n*** MULTI-TURN E2E PASSED ***")


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_empty_message(self, mock_redis):
        """Agent should handle edge cases gracefully."""
        app = _make_app(mock_redis)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t", timeout=60) as c:
            resp = await c.post("/api/agent/chat", json={
                "messages": [{"role": "user", "content": ""}],
            })

        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        types = _types(events)
        # Should at least have session_init and done
        assert "session_init" in types
        assert "done" in types

    @pytest.mark.asyncio
    async def test_invalid_body_returns_422(self, mock_redis):
        app = _make_app(mock_redis)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t", timeout=30) as c:
            resp = await c.post("/api/agent/chat", json={"messages": []})
        assert resp.status_code == 422
