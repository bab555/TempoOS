# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""Unit tests for SSE utility functions."""

import json

from tempo_os.api.sse import (
    sse_done,
    sse_error,
    sse_event,
    sse_message,
    sse_thinking,
    sse_ui_render,
)


class TestSseEvent:
    def test_basic_dict_payload(self):
        result = sse_event("test", {"key": "value"})
        assert result.startswith("event: test\n")
        assert "data: " in result
        assert result.endswith("\n\n")
        data = json.loads(result.split("data: ")[1].strip())
        assert data["key"] == "value"

    def test_string_payload(self):
        result = sse_event("msg", "hello")
        assert "data: hello\n\n" in result

    def test_chinese_content(self):
        result = sse_event("test", {"content": "你好世界"})
        assert "你好世界" in result

    def test_empty_dict(self):
        result = sse_event("test", {})
        assert "data: {}\n\n" in result


class TestSseShortcuts:
    def test_message(self):
        result = sse_message("hello")
        assert "event: message\n" in result
        data = json.loads(result.split("data: ")[1].strip())
        assert data["content"] == "hello"

    def test_thinking(self):
        result = sse_thinking("处理中...")
        assert "event: thinking\n" in result
        data = json.loads(result.split("data: ")[1].strip())
        assert data["content"] == "处理中..."

    def test_ui_render_without_actions(self):
        result = sse_ui_render("smart_table", "测试表", {"rows": []})
        assert "event: ui_render\n" in result
        data = json.loads(result.split("data: ")[1].strip())
        assert data["component"] == "smart_table"
        assert data["title"] == "测试表"
        assert "actions" not in data

    def test_ui_render_with_actions(self):
        result = sse_ui_render("smart_table", "表", {"rows": []}, actions=[{"label": "导出"}])
        data = json.loads(result.split("data: ")[1].strip())
        assert len(data["actions"]) == 1

    def test_error(self):
        result = sse_error("出错了")
        assert "event: error\n" in result
        data = json.loads(result.split("data: ")[1].strip())
        assert data["message"] == "出错了"

    def test_done_basic(self):
        result = sse_done("session-123")
        assert "event: done\n" in result
        data = json.loads(result.split("data: ")[1].strip())
        assert data["session_id"] == "session-123"
        assert "usage" not in data

    def test_done_with_usage(self):
        result = sse_done("s1", usage={"tokens": 100})
        data = json.loads(result.split("data: ")[1].strip())
        assert data["usage"]["tokens"] == 100
