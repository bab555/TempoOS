# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for built-in nodes."""

import pytest
from tempo_os.memory.blackboard import TenantBlackboard
from tempo_os.nodes.echo import EchoNode
from tempo_os.nodes.conditional import ConditionalNode
from tempo_os.nodes.transform import TransformNode
from tempo_os.nodes.notification import NotificationNode


class TestEchoNode:
    @pytest.mark.asyncio
    async def test_echo(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        node = EchoNode()
        result = await node.execute("s1", "test_tenant", {"input": "hello"}, bb)
        assert result.is_success
        assert result.result["echo"] == "hello"
        assert result.ui_schema is not None

    @pytest.mark.asyncio
    async def test_echo_empty(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        node = EchoNode()
        result = await node.execute("s1", "test_tenant", {}, bb)
        assert result.is_success


class TestConditionalNode:
    @pytest.mark.asyncio
    async def test_condition_exists_true(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        await bb.set_state("s1", "has_data", True)

        node = ConditionalNode()
        result = await node.execute("s1", "test_tenant", {
            "key": "has_data", "operator": "exists",
            "true_event": "GO", "false_event": "SKIP",
        }, bb)
        assert result.result["condition_met"] is True
        assert result.next_events == ["GO"]

    @pytest.mark.asyncio
    async def test_condition_exists_false(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        node = ConditionalNode()
        result = await node.execute("s1", "test_tenant", {
            "key": "missing_key", "operator": "exists",
            "true_event": "GO", "false_event": "SKIP",
        }, bb)
        assert result.result["condition_met"] is False
        assert result.next_events == ["SKIP"]

    @pytest.mark.asyncio
    async def test_condition_eq(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        await bb.set_state("s1", "status", "approved")

        node = ConditionalNode()
        result = await node.execute("s1", "test_tenant", {
            "key": "status", "operator": "eq", "value": "approved",
            "true_event": "PROCEED", "false_event": "REJECT",
        }, bb)
        assert result.result["condition_met"] is True


class TestTransformNode:
    @pytest.mark.asyncio
    async def test_extract_from_artifact(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        await bb.push_artifact("s1", "source_data", {
            "items": [{"name": "Widget", "price": 10}]
        })

        node = TransformNode()
        result = await node.execute("s1", "test_tenant", {
            "source_artifact": "source_data",
            "extract_path": "items.0.name",
            "output_key": "item_name",
        }, bb)
        assert result.is_success
        assert result.result["extracted"] == "Widget"

    @pytest.mark.asyncio
    async def test_missing_source(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        node = TransformNode()
        result = await node.execute("s1", "test_tenant", {
            "source_artifact": "nonexistent",
        }, bb)
        assert result.status == "error"


class TestNotificationNode:
    @pytest.mark.asyncio
    async def test_notification(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        node = NotificationNode()
        result = await node.execute("s1", "test_tenant", {
            "message": "Task completed!", "level": "success",
        }, bb)
        assert result.is_success
        assert result.ui_schema["components"][0]["type"] == "chat_message"
