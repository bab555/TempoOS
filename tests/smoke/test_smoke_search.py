# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Smoke test -- Real DashScope web search via SearchNode.

Requires:
  - DASHSCOPE_API_KEY in environment / .env
  - Network access to DashScope API

Run:  pytest tests/smoke/test_smoke_search.py -v -s --timeout=120
"""

import json

import pytest

from tempo_os.core.config import settings

pytestmark = pytest.mark.skipif(
    not settings.DASHSCOPE_API_KEY,
    reason="DASHSCOPE_API_KEY not configured",
)


class TestRealSearchCall:
    @pytest.mark.asyncio
    async def test_search_call_returns_content(self):
        from tempo_os.nodes.search import _search_call

        result = await _search_call(
            api_key=settings.DASHSCOPE_API_KEY,
            model=settings.DASHSCOPE_SEARCH_MODEL,
            messages=[
                {"role": "system", "content": "You are a procurement assistant. Return JSON."},
                {"role": "user", "content": "ThinkPad X1 Carbon price comparison, list 3 models"},
            ],
            search_strategy="max",
        )

        assert result is not None, "DashScope returned None"
        assert "content" in result
        assert len(result["content"]) > 10

        print(f"\n--- Content (500c) ---\n{result['content'][:500]}")
        print(f"--- Search refs: {len(result.get('search_results', []))} ---")


class TestSearchNodeFull:
    @pytest.mark.asyncio
    async def test_product_comparison(self, mock_redis):
        from tempo_os.nodes.search import SearchNode
        from tempo_os.memory.blackboard import TenantBlackboard

        node = SearchNode()
        bb = TenantBlackboard(mock_redis, "smoke")

        result = await node.execute("s_search_1", "smoke", {
            "query": "A4 copy paper price comparison, top 3 brands on Taobao",
            "output_format": "table",
        }, bb)

        print(f"\n--- Status: {result.status}, type: {result.result.get('type')} ---")
        assert result.is_success, f"Failed: {result.error_message}"
        assert result.result.get("type") in ("table", "text")
        assert result.ui_schema is not None

        stored = await bb.get_state("s_search_1", "last_search_result")
        assert stored is not None, "Blackboard empty"
        stored_query = await bb.get_state("s_search_1", "last_search_query")
        assert stored_query is not None

    @pytest.mark.asyncio
    async def test_general_query(self, mock_redis):
        from tempo_os.nodes.search import SearchNode
        from tempo_os.memory.blackboard import TenantBlackboard

        node = SearchNode()
        bb = TenantBlackboard(mock_redis, "smoke")

        result = await node.execute("s_search_2", "smoke", {
            "query": "What certifications are needed for office furniture suppliers in China?",
        }, bb)

        assert result.is_success, f"Failed: {result.error_message}"
        assert result.result.get("type") in ("table", "text")
        print(f"\n--- General query result type: {result.result.get('type')} ---")
