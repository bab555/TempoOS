# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""Unit tests for ChatStore and ChatMessage."""

import json
import pytest

from tempo_os.memory.chat_store import ChatMessage, ChatStore


class TestChatMessage:
    def test_roundtrip_json(self):
        msg = ChatMessage(role="user", content="你好")
        raw = msg.to_json()
        restored = ChatMessage.from_json(raw)
        assert restored.role == "user"
        assert restored.content == "你好"
        assert restored.id == msg.id
        assert restored.ts == msg.ts

    def test_to_dict_minimal(self):
        msg = ChatMessage(role="assistant", content="好的")
        d = msg.to_dict()
        assert d["role"] == "assistant"
        assert d["content"] == "好的"
        assert "tool_name" not in d
        assert "files" not in d

    def test_to_dict_with_tool(self):
        msg = ChatMessage(
            role="tool",
            content='{"result": 42}',
            tool_name="search",
            tool_call_id="tc_001",
        )
        d = msg.to_dict()
        assert d["tool_name"] == "search"
        assert d["tool_call_id"] == "tc_001"

    def test_to_llm_message_user(self):
        msg = ChatMessage(role="user", content="查询")
        llm = msg.to_llm_message()
        assert llm == {"role": "user", "content": "查询"}

    def test_to_llm_message_tool(self):
        msg = ChatMessage(role="tool", content="result", tool_name="search")
        llm = msg.to_llm_message()
        assert llm["name"] == "search"

    def test_from_dict_defaults(self):
        d = {"role": "user", "content": "hi"}
        msg = ChatMessage.from_dict(d)
        assert msg.type == "text"
        assert msg.id is not None
        assert msg.ts is not None


class TestChatMessageFiles:
    def test_with_files(self):
        msg = ChatMessage(
            role="user",
            content="请分析",
            files=[{"name": "report.xlsx", "url": "https://oss/report.xlsx"}],
        )
        d = msg.to_dict()
        assert len(d["files"]) == 1
        assert d["files"][0]["name"] == "report.xlsx"

    def test_with_ui_schema(self):
        msg = ChatMessage(
            role="assistant",
            content="结果如下",
            ui_schema={"component": "table", "data": []},
        )
        d = msg.to_dict()
        assert d["ui_schema"]["component"] == "table"

    def test_with_extra(self):
        msg = ChatMessage(
            role="assistant",
            content="done",
            extra={"scene": "procurement", "round": 2},
        )
        d = msg.to_dict()
        assert d["extra"]["scene"] == "procurement"


class TestChatStoreRedis:
    """Test ChatStore operations against FakeRedis."""

    @pytest.mark.asyncio
    async def test_append_and_count(self, mock_redis):
        store = ChatStore(mock_redis, "test_tenant", ttl=3600)
        msg = ChatMessage(role="user", content="hello")
        n = await store.append("s_001", msg)
        assert n == 1
        assert await store.count("s_001") == 1

    @pytest.mark.asyncio
    async def test_append_batch(self, mock_redis):
        store = ChatStore(mock_redis, "test_tenant", ttl=3600)
        msgs = [
            ChatMessage(role="user", content="q1"),
            ChatMessage(role="assistant", content="a1"),
        ]
        n = await store.append_batch("s_001", msgs)
        assert n == 2
        assert await store.count("s_001") == 2

    @pytest.mark.asyncio
    async def test_get_all(self, mock_redis):
        store = ChatStore(mock_redis, "test_tenant", ttl=3600)
        await store.append("s_001", ChatMessage(role="user", content="first"))
        await store.append("s_001", ChatMessage(role="assistant", content="second"))
        all_msgs = await store.get_all("s_001")
        assert len(all_msgs) == 2
        assert all_msgs[0].role == "user"
        assert all_msgs[0].content == "first"
        assert all_msgs[1].role == "assistant"

    @pytest.mark.asyncio
    async def test_get_recent(self, mock_redis):
        store = ChatStore(mock_redis, "test_tenant", ttl=3600)
        for i in range(10):
            await store.append("s_001", ChatMessage(role="user", content=f"msg_{i}"))
        recent = await store.get_recent("s_001", n=3)
        assert len(recent) == 3
        assert recent[0].content == "msg_7"
        assert recent[2].content == "msg_9"

    @pytest.mark.asyncio
    async def test_get_history_pagination(self, mock_redis):
        store = ChatStore(mock_redis, "test_tenant", ttl=3600)
        for i in range(5):
            await store.append("s_001", ChatMessage(role="user", content=f"msg_{i}"))
        page = await store.get_history("s_001", offset=2, limit=2)
        assert len(page) == 2
        assert page[0].content == "msg_2"
        assert page[1].content == "msg_3"

    @pytest.mark.asyncio
    async def test_clear(self, mock_redis):
        store = ChatStore(mock_redis, "test_tenant", ttl=3600)
        await store.append("s_001", ChatMessage(role="user", content="hi"))
        await store.clear("s_001")
        assert await store.count("s_001") == 0

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, mock_redis):
        store_a = ChatStore(mock_redis, "tenant_a", ttl=3600)
        store_b = ChatStore(mock_redis, "tenant_b", ttl=3600)
        await store_a.append("s_001", ChatMessage(role="user", content="from_a"))
        assert await store_b.count("s_001") == 0

    @pytest.mark.asyncio
    async def test_session_isolation(self, mock_redis):
        store = ChatStore(mock_redis, "test_tenant", ttl=3600)
        await store.append("s_001", ChatMessage(role="user", content="session_1"))
        await store.append("s_002", ChatMessage(role="user", content="session_2"))
        assert await store.count("s_001") == 1
        assert await store.count("s_002") == 1

    @pytest.mark.asyncio
    async def test_empty_batch_noop(self, mock_redis):
        store = ChatStore(mock_redis, "test_tenant", ttl=3600)
        n = await store.append_batch("s_001", [])
        assert n == 0
