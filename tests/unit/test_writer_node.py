# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""Unit tests for WriterNode (with mocked DashScope)."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from tempo_os.memory.blackboard import TenantBlackboard
from tempo_os.nodes.writer import WriterNode, _load_skill_prompt, _parse_writer_output, _SKILL_CACHE, SKILL_KEYS


class TestSkillPromptLoading:
    def test_all_skills_cached(self):
        for key in SKILL_KEYS:
            assert key in _SKILL_CACHE, f"Skill '{key}' not found in cache"
            assert len(_SKILL_CACHE[key]) > 50, f"Skill '{key}' prompt too short"

    def test_load_nonexistent_skill(self):
        result = _load_skill_prompt("this_does_not_exist_xyz")
        assert result is None

    def test_each_skill_has_json_instruction(self):
        for key, prompt in _SKILL_CACHE.items():
            assert "JSON" in prompt or "json" in prompt, f"Skill '{key}' missing JSON output instruction"


class TestParseWriterOutput:
    def test_valid_table(self):
        content = json.dumps({
            "type": "table",
            "title": "报价表",
            "columns": [{"key": "product", "label": "产品"}],
            "rows": [{"product": "笔记本"}],
        })
        result = _parse_writer_output(content, "quotation")
        assert result["type"] == "table"
        assert result["skill"] == "quotation"

    def test_valid_document(self):
        content = json.dumps({
            "type": "document",
            "title": "合同",
            "sections": [{"title": "第一条", "content": "..."}],
            "fields": {"party_a": "甲方"},
        })
        result = _parse_writer_output(content, "contract")
        assert result["type"] == "document"

    def test_markdown_wrapped_json(self):
        inner = json.dumps({"type": "table", "title": "t", "columns": [], "rows": []})
        content = f"```json\n{inner}\n```"
        result = _parse_writer_output(content, "general")
        assert result["type"] == "table"

    def test_plain_text_fallback(self):
        result = _parse_writer_output("这不是JSON", "contract")
        assert result["type"] == "document"
        assert result["skill"] == "contract"
        assert len(result["sections"]) == 1

    def test_report_type(self):
        content = json.dumps({
            "type": "report",
            "title": "月报",
            "metrics": [{"label": "总额", "value": "100万"}],
        })
        result = _parse_writer_output(content, "financial_report")
        assert result["type"] == "report"


class TestWriterNodeExecute:
    @pytest.mark.asyncio
    async def test_missing_api_key(self, mock_redis):
        node = WriterNode()
        bb = TenantBlackboard(mock_redis, "test")
        with patch("tempo_os.nodes.writer.settings") as ms:
            ms.DASHSCOPE_API_KEY = ""
            ms.DASHSCOPE_MODEL = "qwen3-max"
            result = await node.execute("s1", "test", {"skill": "contract", "data": {"a": 1}}, bb)
        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_no_data_returns_need_input(self, mock_redis):
        node = WriterNode()
        bb = TenantBlackboard(mock_redis, "test")
        result = await node.execute("s1", "test", {"skill": "contract"}, bb)
        assert result.status == "need_user_input"

    @pytest.mark.asyncio
    async def test_success_quotation(self, mock_redis):
        node = WriterNode()
        bb = TenantBlackboard(mock_redis, "test")

        mock_content = json.dumps({
            "type": "table",
            "title": "报价表",
            "meta": {"quotation_no": "QT-20260215-001"},
            "columns": [
                {"key": "product", "label": "产品"},
                {"key": "price", "label": "单价"},
            ],
            "rows": [{"product": "ThinkPad X1", "price": "9999"}],
            "summary": {"total_amount": "9999"},
        })

        with patch("tempo_os.nodes.writer._writer_call", new_callable=AsyncMock, return_value=mock_content):
            with patch("tempo_os.nodes.writer.settings") as ms:
                ms.DASHSCOPE_API_KEY = "test-key"
                ms.DASHSCOPE_MODEL = "qwen3-max"
                result = await node.execute("s1", "test", {
                    "skill": "quotation",
                    "data": {"items": [{"name": "ThinkPad X1", "qty": 1}]},
                }, bb)

        assert result.is_success
        assert result.result["type"] == "table"
        assert result.ui_schema["component"] == "smart_table"
        assert "生成合同" in str(result.ui_schema["actions"])

    @pytest.mark.asyncio
    async def test_success_contract(self, mock_redis):
        node = WriterNode()
        bb = TenantBlackboard(mock_redis, "test")

        mock_content = json.dumps({
            "type": "document",
            "title": "采购合同",
            "sections": [{"title": "第一条", "content": "本合同..."}],
            "fields": {"party_a": "中建四局", "amount": "50万"},
        })

        with patch("tempo_os.nodes.writer._writer_call", new_callable=AsyncMock, return_value=mock_content):
            with patch("tempo_os.nodes.writer.settings") as ms:
                ms.DASHSCOPE_API_KEY = "test-key"
                ms.DASHSCOPE_MODEL = "qwen3-max"
                result = await node.execute("s1", "test", {
                    "skill": "contract",
                    "data": {"party_a": "中建四局", "items": []},
                }, bb)

        assert result.is_success
        assert result.result["type"] == "document"
        assert result.ui_schema["component"] == "document_preview"
        assert "生成送货单" in str(result.ui_schema["actions"])

    @pytest.mark.asyncio
    async def test_uses_blackboard_search_result(self, mock_redis):
        """WriterNode should read previous search results from Blackboard."""
        node = WriterNode()
        bb = TenantBlackboard(mock_redis, "test")

        await bb.set_state("s1", "last_search_result", {
            "type": "table",
            "rows": [{"supplier": "京东", "price": 9999}],
        })

        mock_content = json.dumps({
            "type": "table", "title": "比价表",
            "columns": [], "rows": [{"supplier": "京东"}],
        })

        with patch("tempo_os.nodes.writer._writer_call", new_callable=AsyncMock, return_value=mock_content) as mock_call:
            with patch("tempo_os.nodes.writer.settings") as ms:
                ms.DASHSCOPE_API_KEY = "test-key"
                ms.DASHSCOPE_MODEL = "qwen3-max"
                result = await node.execute("s1", "test", {"skill": "comparison"}, bb)

        assert result.is_success
        # Verify the LLM was called with search result context
        call_args = mock_call.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages") or call_args[0][2]
        user_msg = [m for m in messages if m["role"] == "user"][0]
        assert "搜索结果数据" in user_msg["content"]

    @pytest.mark.asyncio
    async def test_unknown_skill_fallback(self, mock_redis):
        node = WriterNode()
        bb = TenantBlackboard(mock_redis, "test")

        mock_content = json.dumps({"type": "document", "title": "doc", "sections": [], "fields": {}})

        with patch("tempo_os.nodes.writer._writer_call", new_callable=AsyncMock, return_value=mock_content):
            with patch("tempo_os.nodes.writer.settings") as ms:
                ms.DASHSCOPE_API_KEY = "test-key"
                ms.DASHSCOPE_MODEL = "qwen3-max"
                result = await node.execute("s1", "test", {
                    "skill": "nonexistent_skill",
                    "data": {"x": 1},
                }, bb)

        assert result.is_success
