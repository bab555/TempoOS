# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""Unit tests for Agent Controller — request model validation and internal helpers."""

import json
import pytest

from tempo_os.api.agent import (
    AgentChatRequest,
    FileRef,
    UserMessage,
    _build_llm_messages,
    _collect_files,
    _enrich_ui_render,
    _result_to_ui,
    _tool_display_name,
)
from tempo_os.agents.prompt_loader import get_scene_prompt, DEFAULT_SCENE


class TestRequestModels:
    def test_minimal_request(self):
        req = AgentChatRequest(
            messages=[UserMessage(content="你好")]
        )
        assert req.session_id is None
        assert len(req.messages) == 1
        assert req.messages[0].role == "user"

    def test_with_session_and_files(self):
        req = AgentChatRequest(
            session_id="abc-123",
            messages=[
                UserMessage(
                    content="请分析这个文件",
                    files=[FileRef(name="report.xlsx", url="https://oss/report.xlsx", type="application/xlsx")],
                )
            ],
        )
        assert req.session_id == "abc-123"
        assert len(req.messages[0].files) == 1
        assert req.messages[0].files[0].name == "report.xlsx"

    def test_empty_messages_rejected(self):
        with pytest.raises(Exception):
            AgentChatRequest(messages=[])


class TestCollectFiles:
    def test_no_files(self):
        msgs = [UserMessage(content="hello")]
        assert _collect_files(msgs) == []

    def test_files_from_user_only(self):
        msgs = [
            UserMessage(role="system", content="sys", files=[FileRef(name="sys.txt", url="u1")]),
            UserMessage(role="user", content="hi", files=[FileRef(name="a.pdf", url="u2")]),
        ]
        result = _collect_files(msgs)
        assert len(result) == 1
        assert result[0].name == "a.pdf"

    def test_multiple_user_messages(self):
        msgs = [
            UserMessage(content="first", files=[FileRef(name="a.pdf", url="u1")]),
            UserMessage(content="second", files=[FileRef(name="b.pdf", url="u2"), FileRef(name="c.pdf", url="u3")]),
        ]
        result = _collect_files(msgs)
        assert len(result) == 3


class TestBuildLlmMessages:
    def test_basic_messages(self):
        prompt = get_scene_prompt(DEFAULT_SCENE)
        msgs = [UserMessage(content="你好")]
        result = _build_llm_messages(msgs, prompt)
        assert result[0]["role"] == "system"
        assert result[0]["content"] == prompt
        assert result[1]["role"] == "user"
        assert result[1]["content"] == "你好"

    def test_file_text_injection(self):
        prompt = get_scene_prompt(DEFAULT_SCENE)
        msgs = [
            UserMessage(
                content="请分析",
                files=[FileRef(name="report.xlsx", url="https://oss/report.xlsx")],
            )
        ]
        file_texts = {"https://oss/report.xlsx": "表格内容:\n产品A, 100元"}
        result = _build_llm_messages(msgs, prompt, file_texts)
        user_msg = result[1]["content"]
        assert "附件内容" in user_msg
        assert "report.xlsx" in user_msg
        assert "产品A" in user_msg

    def test_file_not_ready(self):
        prompt = get_scene_prompt(DEFAULT_SCENE)
        msgs = [
            UserMessage(
                content="请分析",
                files=[FileRef(name="slow.pdf", url="https://oss/slow.pdf")],
            )
        ]
        result = _build_llm_messages(msgs, prompt, file_texts={})
        user_msg = result[1]["content"]
        assert "处理中" in user_msg



class TestToolDisplayName:
    def test_known(self):
        assert _tool_display_name("search") == "联网搜索"
        assert _tool_display_name("writer") == "智能撰写"
        assert _tool_display_name("data_query") == "数据检索"

    def test_unknown(self):
        assert _tool_display_name("unknown_tool") == "unknown_tool"


class TestEnrichUiRender:
    def test_dict_enriched(self):
        ui = {"component": "smart_table", "title": "test"}
        result = _enrich_ui_render(ui, ui_id="panel_main", render_mode="replace", schema_version=1, run_id="r1")
        assert result["ui_id"] == "panel_main"
        assert result["render_mode"] == "replace"
        assert result["schema_version"] == 1
        assert result["run_id"] == "r1"
        assert result["component"] == "smart_table"

    def test_existing_keys_preserved(self):
        ui = {"component": "smart_table", "ui_id": "custom_panel", "render_mode": "append"}
        result = _enrich_ui_render(ui, ui_id="default", render_mode="replace", schema_version=1)
        assert result["ui_id"] == "custom_panel"
        assert result["render_mode"] == "append"

    def test_non_dict_wrapped(self):
        result = _enrich_ui_render("not a dict", ui_id="p", render_mode="replace", schema_version=1)
        assert result["component"] == "raw_json"


class TestResultToUi:
    def test_table(self):
        result = {"type": "table", "title": "比价", "columns": [{"key": "a"}], "rows": [{"a": 1}]}
        ui = _result_to_ui("search", result)
        assert ui["component"] == "smart_table"

    def test_document(self):
        result = {"type": "document", "title": "合同"}
        ui = _result_to_ui("writer", result)
        assert ui["component"] == "document_preview"

    def test_report(self):
        result = {"type": "report", "title": "月报"}
        ui = _result_to_ui("writer", result)
        assert ui["component"] == "chart_report"

    def test_none_and_empty_input(self):
        assert _result_to_ui("search", None) is None
        assert _result_to_ui("search", {}) is None  # empty dict is falsy, returns None

    def test_generic_fallback(self):
        result = {"type": "something_else", "data": 42}
        ui = _result_to_ui("search", result)
        assert ui["component"] == "smart_table"
