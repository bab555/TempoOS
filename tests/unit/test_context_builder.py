# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""Unit tests for ContextBuilder — V1 trim and boundary detection."""

import pytest

from tempo_os.memory.chat_store import ChatMessage
from tempo_os.memory.context_builder import ContextBuilder


def _make_messages(count: int) -> list[ChatMessage]:
    """Generate alternating user/assistant messages."""
    msgs = []
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append(ChatMessage(role=role, content=f"msg_{i}"))
    return msgs


class TestFindRecentBoundary:
    """Test the _find_recent_boundary method in isolation."""

    def _builder(self, max_rounds: int = 3) -> ContextBuilder:
        return ContextBuilder(
            chat_store=None,  # type: ignore
            blackboard=None,  # type: ignore
            max_recent_rounds=max_rounds,
        )

    def test_short_history_all_recent(self):
        builder = self._builder(max_rounds=5)
        msgs = _make_messages(4)  # 2 user messages
        boundary = builder._find_recent_boundary(msgs)
        assert boundary == 0  # everything is recent

    def test_boundary_at_correct_user_message(self):
        builder = self._builder(max_rounds=2)
        # 8 messages = 4 user + 4 assistant
        msgs = _make_messages(8)
        boundary = builder._find_recent_boundary(msgs)
        # Last 2 user messages are at index 4 and 6
        # So boundary should be at index 4
        assert boundary == 4

    def test_single_message(self):
        builder = self._builder(max_rounds=3)
        msgs = [ChatMessage(role="user", content="hello")]
        boundary = builder._find_recent_boundary(msgs)
        assert boundary == 0


class TestV1Trim:
    def _builder(self) -> ContextBuilder:
        return ContextBuilder(
            chat_store=None,  # type: ignore
            blackboard=None,  # type: ignore
        )

    def test_filters_tool_messages(self):
        builder = self._builder()
        msgs = [
            ChatMessage(role="user", content="search for X"),
            ChatMessage(role="assistant", content="calling search", msg_type="tool_call", tool_name="search"),
            ChatMessage(role="tool", content='{"results": []}', msg_type="tool_result", tool_name="search"),
            ChatMessage(role="assistant", content="Here are the results"),
        ]
        trimmed = builder._v1_trim(msgs)
        # Only user and assistant text messages should remain
        assert len(trimmed) == 2
        assert trimmed[0]["role"] == "user"
        assert trimmed[1]["role"] == "assistant"

    def test_truncates_long_content(self):
        builder = self._builder()
        long_text = "a" * 500
        msgs = [ChatMessage(role="user", content=long_text)]
        trimmed = builder._v1_trim(msgs)
        assert len(trimmed[0]["content"]) == 203  # 200 + "..."

    def test_empty_input(self):
        builder = self._builder()
        assert builder._v1_trim([]) == []


class TestFormatForSummary:
    def test_formats_roles_correctly(self):
        msgs = [
            ChatMessage(role="user", content="查询产品A"),
            ChatMessage(role="assistant", content="好的，正在查询"),
            ChatMessage(role="tool", content='{"data": []}', tool_name="search"),
        ]
        text = ContextBuilder._format_for_summary(msgs)
        assert "用户: 查询产品A" in text
        assert "助手: 好的，正在查询" in text
        assert "[工具 search]" in text

    def test_truncates_long_messages(self):
        msgs = [
            ChatMessage(role="user", content="x" * 500),
        ]
        text = ContextBuilder._format_for_summary(msgs)
        assert len(text) <= 310  # 300 char limit + "用户: " prefix


class TestContextBuilderBuild:
    """Integration test: ContextBuilder.build() with real ChatStore on FakeRedis."""

    @pytest.mark.asyncio
    async def test_build_empty_history(self, mock_redis):
        from tempo_os.memory.chat_store import ChatStore
        from tempo_os.memory.blackboard import TenantBlackboard

        store = ChatStore(mock_redis, "test_tenant", ttl=3600)
        bb = TenantBlackboard(mock_redis, "test_tenant")
        builder = ContextBuilder(chat_store=store, blackboard=bb)

        msgs = await builder.build("s_empty", "You are a helpful assistant.")
        assert len(msgs) == 1
        assert msgs[0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_build_short_history_no_trim(self, mock_redis):
        from tempo_os.memory.chat_store import ChatStore
        from tempo_os.memory.blackboard import TenantBlackboard

        store = ChatStore(mock_redis, "test_tenant", ttl=3600)
        bb = TenantBlackboard(mock_redis, "test_tenant")
        builder = ContextBuilder(
            chat_store=store, blackboard=bb, max_recent_rounds=5,
        )

        await store.append("s_001", ChatMessage(role="user", content="hello"))
        await store.append("s_001", ChatMessage(role="assistant", content="hi there"))

        msgs = await builder.build("s_001", "System prompt")
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "System prompt"
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "hello"
        assert msgs[2]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_build_long_history_trims_old(self, mock_redis):
        from tempo_os.memory.chat_store import ChatStore
        from tempo_os.memory.blackboard import TenantBlackboard

        store = ChatStore(mock_redis, "test_tenant", ttl=3600)
        bb = TenantBlackboard(mock_redis, "test_tenant")
        builder = ContextBuilder(
            chat_store=store, blackboard=bb,
            max_recent_rounds=2, summary_threshold=100,
        )

        # 10 rounds = 20 messages
        for i in range(10):
            await store.append("s_001", ChatMessage(role="user", content=f"question_{i}"))
            await store.append("s_001", ChatMessage(role="assistant", content=f"answer_{i}"))

        msgs = await builder.build("s_001", "System prompt")
        # Should have: system + trimmed old + recent 2 rounds (4 msgs)
        assert msgs[0]["role"] == "system"
        # Recent messages should be the last 2 rounds
        recent_user_msgs = [m for m in msgs if m["role"] == "user" and "question_" in m.get("content", "")]
        assert any("question_9" in m["content"] for m in recent_user_msgs)
        assert any("question_8" in m["content"] for m in recent_user_msgs)
