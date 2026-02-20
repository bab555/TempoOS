# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""Unit tests for SearchNode (with mocked DashScope)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tempo_os.memory.blackboard import TenantBlackboard
from tempo_os.nodes.search import SearchNode, _parse_search_result, _build_search_ui


class TestParseSearchResult:
    def test_valid_table_json(self):
        content = json.dumps({
            "type": "table",
            "title": "比价表",
            "columns": [{"key": "name", "label": "名称"}],
            "rows": [{"name": "产品A"}],
        })
        result = _parse_search_result(content, [])
        assert result["type"] == "table"
        assert len(result["rows"]) == 1

    def test_json_with_markdown_wrapper(self):
        content = '```json\n{"type": "table", "title": "t", "columns": [], "rows": []}\n```'
        result = _parse_search_result(content, [])
        assert result["type"] == "table"

    def test_plain_text_fallback(self):
        content = "这是一段搜索结果的文字描述。"
        result = _parse_search_result(content, [])
        assert result["type"] == "text"
        assert "搜索结果" in result["content"]

    def test_search_references_attached(self):
        content = json.dumps({"type": "table", "title": "t", "columns": [], "rows": []})
        refs = [{"title": "淘宝", "url": "https://taobao.com", "index": "1"}]
        result = _parse_search_result(content, refs)
        assert len(result["sources"]) == 1


class TestBuildSearchUi:
    def test_table_ui(self):
        data = {
            "type": "table",
            "title": "比价表",
            "columns": [{"key": "a", "label": "A"}],
            "rows": [{"a": "1"}],
        }
        ui = _build_search_ui(data, [])
        assert ui["component"] == "smart_table"
        assert "导出 Excel" in str(ui["actions"])

    def test_text_ui(self):
        data = {"type": "text", "title": "结果", "content": "some text"}
        ui = _build_search_ui(data, [])
        assert ui["component"] == "document_preview"


class TestSearchNodeExecute:
    @pytest.mark.asyncio
    async def test_missing_query(self, mock_redis):
        node = SearchNode()
        bb = TenantBlackboard(mock_redis, "test")
        result = await node.execute("s1", "test", {}, bb)
        assert result.status == "error"
        assert "query" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_missing_api_key(self, mock_redis):
        node = SearchNode()
        bb = TenantBlackboard(mock_redis, "test")
        with patch("tempo_os.nodes.search.settings") as mock_settings:
            mock_settings.DASHSCOPE_API_KEY = ""
            mock_settings.DASHSCOPE_SEARCH_MODEL = "qwen3.5-plus"
            result = await node.execute("s1", "test", {"query": "test"}, bb)
        assert result.status == "error"
        assert "API_KEY" in result.error_message

    @pytest.mark.asyncio
    async def test_success_with_mock(self, mock_redis):
        node = SearchNode()
        bb = TenantBlackboard(mock_redis, "test")

        mock_response = {
            "content": json.dumps({
                "type": "table",
                "title": "笔记本比价",
                "columns": [{"key": "name", "label": "名称"}, {"key": "price", "label": "价格"}],
                "rows": [{"name": "ThinkPad", "price": "8999"}],
            }),
            "search_results": [{"title": "京东", "url": "https://jd.com", "index": "1"}],
        }

        with patch("tempo_os.nodes.search._search_call", new_callable=AsyncMock, return_value=mock_response):
            with patch("tempo_os.nodes.search.settings") as ms:
                ms.DASHSCOPE_API_KEY = "test-key"
                ms.DASHSCOPE_SEARCH_MODEL = "qwen3.5-plus"
                result = await node.execute("s1", "test", {"query": "ThinkPad笔记本"}, bb)

        assert result.is_success
        assert result.result["type"] == "table"
        assert result.ui_schema["component"] == "smart_table"
        assert len(result.result["sources"]) == 1

        # Check Blackboard state
        stored = await bb.get_state("s1", "last_search_result")
        assert stored is not None
