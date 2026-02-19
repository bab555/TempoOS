# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for NodeRegistry."""

import pytest
from tempo_os.kernel.node_registry import NodeRegistry, WebhookInfo
from tempo_os.nodes.echo import EchoNode


class TestNodeRegistry:
    def test_register_and_get_builtin(self):
        reg = NodeRegistry()
        reg.register_builtin("echo", EchoNode())
        node = reg.get("echo")
        assert isinstance(node, EchoNode)

    def test_register_and_get_webhook(self):
        reg = NodeRegistry()
        reg.register_webhook("ext_svc", "http://example.com/webhook", name="External")
        wh = reg.get("ext_svc")
        assert isinstance(wh, WebhookInfo)
        assert wh.endpoint == "http://example.com/webhook"

    def test_resolve_builtin_ref(self):
        reg = NodeRegistry()
        reg.register_builtin("echo", EchoNode())
        node = reg.resolve_ref("builtin://echo")
        assert isinstance(node, EchoNode)

    def test_resolve_http_ref(self):
        reg = NodeRegistry()
        reg.register_webhook("ext", "http://example.com/run")
        wh = reg.resolve_ref("http://example.com/run")
        assert isinstance(wh, WebhookInfo)

    def test_resolve_unknown_returns_none(self):
        reg = NodeRegistry()
        assert reg.resolve_ref("builtin://nonexistent") is None

    def test_is_builtin(self):
        reg = NodeRegistry()
        assert reg.is_builtin("builtin://echo") is True
        assert reg.is_builtin("http://example.com") is False

    def test_list_all(self):
        reg = NodeRegistry()
        reg.register_builtin("echo", EchoNode())
        reg.register_webhook("ext", "http://example.com/run", name="Ext")
        listing = reg.list_all()
        assert len(listing) == 2
        types = {item["node_type"] for item in listing}
        assert types == {"builtin", "webhook"}

    def test_list_builtin_ids(self):
        reg = NodeRegistry()
        reg.register_builtin("echo", EchoNode())
        reg.register_builtin("transform", EchoNode())  # reuse for test
        assert reg.list_builtin_ids() == {"echo", "transform"}

    def test_contains(self):
        reg = NodeRegistry()
        reg.register_builtin("echo", EchoNode())
        assert "echo" in reg
        assert "nope" not in reg

    def test_len(self):
        reg = NodeRegistry()
        assert len(reg) == 0
        reg.register_builtin("a", EchoNode())
        reg.register_webhook("b", "http://x.com")
        assert len(reg) == 2
