# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""Unit tests for Tonglu data nodes (data_query, data_ingest, file_parser)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tempo_os.memory.blackboard import TenantBlackboard
from tempo_os.nodes.data_query import DataQueryNode
from tempo_os.nodes.data_ingest import DataIngestNode
from tempo_os.nodes.file_parser import FileParserNode
from tempo_os.runtime.tonglu_client import TongluClient


# ── DataQueryNode ─────────────────────────────────────────────


class TestDataQueryNode:
    @pytest.mark.asyncio
    async def test_query_success(self, mock_redis):
        """DataQueryNode should return results and ui_schema."""
        mock_client = MagicMock(spec=TongluClient)
        mock_client.query = AsyncMock(return_value=[
            {"id": "1", "schema_type": "contract", "party_a": "华为", "amount": 1000000},
            {"id": "2", "schema_type": "contract", "party_a": "腾讯", "amount": 500000},
        ])

        node = DataQueryNode(mock_client)
        bb = TenantBlackboard(mock_redis, "test_tenant")

        result = await node.execute("s1", "test_tenant", {
            "intent": "华为的合同",
            "mode": "hybrid",
        }, bb)

        assert result.is_success
        assert result.result["count"] == 2
        assert len(result.result["records"]) == 2
        assert result.artifacts["query_result"] is not None

        # P0 fix: last_data_query_result should be stored in Blackboard
        stored = await bb.get_state("s1", "last_data_query_result")
        assert stored is not None
        assert stored["count"] == 2

        # Accumulated results
        accumulated = await bb.get_results("s1", "data_query")
        assert len(accumulated) == 1
        assert accumulated[0]["count"] == 2

    @pytest.mark.asyncio
    async def test_multiple_queries_accumulate(self, mock_redis):
        """Multiple data_query calls should accumulate results."""
        mock_client = MagicMock(spec=TongluClient)
        mock_client.query = AsyncMock(side_effect=[
            [{"id": "1", "name": "result_1"}],
            [{"id": "2", "name": "result_2"}, {"id": "3", "name": "result_3"}],
        ])

        node = DataQueryNode(mock_client)
        bb = TenantBlackboard(mock_redis, "test_tenant")

        await node.execute("s1", "test_tenant", {"intent": "query 1"}, bb)
        await node.execute("s1", "test_tenant", {"intent": "query 2"}, bb)

        # last_data_query_result should be the latest
        latest = await bb.get_state("s1", "last_data_query_result")
        assert latest["count"] == 2

        # accumulated should have both
        accumulated = await bb.get_results("s1", "data_query")
        assert len(accumulated) == 2
        assert accumulated[0]["count"] == 1
        assert accumulated[1]["count"] == 2

    @pytest.mark.asyncio
    async def test_query_empty_results(self, mock_redis):
        """DataQueryNode should handle empty results gracefully."""
        mock_client = MagicMock(spec=TongluClient)
        mock_client.query = AsyncMock(return_value=[])

        node = DataQueryNode(mock_client)
        bb = TenantBlackboard(mock_redis, "test_tenant")

        result = await node.execute("s1", "test_tenant", {
            "intent": "不存在的数据",
        }, bb)

        assert result.is_success
        assert result.result["count"] == 0
        # UI schema should show "未找到" message
        assert result.ui_schema["components"][0]["type"] == "text"

    @pytest.mark.asyncio
    async def test_query_with_filters(self, mock_redis):
        """DataQueryNode should pass filters to client."""
        mock_client = MagicMock(spec=TongluClient)
        mock_client.query = AsyncMock(return_value=[])

        node = DataQueryNode(mock_client)
        bb = TenantBlackboard(mock_redis, "test_tenant")

        await node.execute("s1", "test_tenant", {
            "intent": "test",
            "filters": {"schema_type": "invoice"},
            "mode": "sql",
            "limit": 5,
        }, bb)

        mock_client.query.assert_called_once_with(
            intent="test",
            filters={"schema_type": "invoice"},
            tenant_id="test_tenant",
            mode="sql",
            limit=5,
        )

    @pytest.mark.asyncio
    async def test_query_error_handling(self, mock_redis):
        """DataQueryNode should return error status on failure."""
        mock_client = MagicMock(spec=TongluClient)
        mock_client.query = AsyncMock(side_effect=Exception("Connection refused"))

        node = DataQueryNode(mock_client)
        bb = TenantBlackboard(mock_redis, "test_tenant")

        result = await node.execute("s1", "test_tenant", {
            "intent": "test",
        }, bb)

        assert result.status == "error"
        assert "数据查询失败" in result.error_message

    @pytest.mark.asyncio
    async def test_ui_schema_table(self, mock_redis):
        """DataQueryNode should build table UI schema from results."""
        mock_client = MagicMock(spec=TongluClient)
        mock_client.query = AsyncMock(return_value=[
            {"id": "1", "name": "test", "_match_type": "sql"},
        ])

        node = DataQueryNode(mock_client)
        bb = TenantBlackboard(mock_redis, "test_tenant")

        result = await node.execute("s1", "test_tenant", {"intent": "test"}, bb)

        schema = result.ui_schema
        assert schema["components"][0]["type"] == "table"
        # _match_type should be excluded from columns
        column_keys = [c["key"] for c in schema["components"][0]["props"]["columns"]]
        assert "_match_type" not in column_keys

    def test_node_metadata(self):
        """DataQueryNode should have correct metadata."""
        node = DataQueryNode(MagicMock())
        assert node.node_id == "data_query"
        assert node.name == "数据查询"
        info = node.get_info()
        assert info["node_id"] == "data_query"


# ── DataIngestNode ────────────────────────────────────────────


class TestDataIngestNode:
    @pytest.mark.asyncio
    async def test_ingest_from_params(self, mock_redis):
        """DataIngestNode should ingest data from params."""
        mock_client = MagicMock(spec=TongluClient)
        mock_client.ingest = AsyncMock(return_value="rec-123")

        node = DataIngestNode(mock_client)
        bb = TenantBlackboard(mock_redis, "test_tenant")

        result = await node.execute("s1", "test_tenant", {
            "data": {"party_a": "华为", "amount": 1000000},
            "schema_type": "contract",
        }, bb)

        assert result.is_success
        assert result.result["record_id"] == "rec-123"

    @pytest.mark.asyncio
    async def test_ingest_from_blackboard(self, mock_redis):
        """DataIngestNode should read data from Blackboard artifact."""
        mock_client = MagicMock(spec=TongluClient)
        mock_client.ingest = AsyncMock(return_value="rec-456")

        node = DataIngestNode(mock_client)
        bb = TenantBlackboard(mock_redis, "test_tenant")

        # Push artifact to Blackboard
        await bb.push_artifact("s1", "quotation_result", {
            "vendor": "华为", "total": 500000,
        })

        result = await node.execute("s1", "test_tenant", {
            "artifact_key": "quotation_result",
        }, bb)

        assert result.is_success
        assert result.result["record_id"] == "rec-456"

    @pytest.mark.asyncio
    async def test_ingest_missing_artifact(self, mock_redis):
        """DataIngestNode should error when artifact not found."""
        mock_client = MagicMock(spec=TongluClient)

        node = DataIngestNode(mock_client)
        bb = TenantBlackboard(mock_redis, "test_tenant")

        result = await node.execute("s1", "test_tenant", {
            "artifact_key": "nonexistent",
        }, bb)

        assert result.status == "error"
        assert "not found" in result.error_message

    @pytest.mark.asyncio
    async def test_ingest_no_data(self, mock_redis):
        """DataIngestNode should error when no data provided."""
        mock_client = MagicMock(spec=TongluClient)

        node = DataIngestNode(mock_client)
        bb = TenantBlackboard(mock_redis, "test_tenant")

        result = await node.execute("s1", "test_tenant", {}, bb)

        assert result.status == "error"
        assert "No data provided" in result.error_message

    @pytest.mark.asyncio
    async def test_ingest_error_handling(self, mock_redis):
        """DataIngestNode should handle API errors."""
        mock_client = MagicMock(spec=TongluClient)
        mock_client.ingest = AsyncMock(side_effect=Exception("API error"))

        node = DataIngestNode(mock_client)
        bb = TenantBlackboard(mock_redis, "test_tenant")

        result = await node.execute("s1", "test_tenant", {
            "data": {"test": True},
        }, bb)

        assert result.status == "error"
        assert "数据写入失败" in result.error_message

    def test_node_metadata(self):
        """DataIngestNode should have correct metadata."""
        node = DataIngestNode(MagicMock())
        assert node.node_id == "data_ingest"
        assert node.name == "数据写入"


# ── FileParserNode ────────────────────────────────────────────


class TestFileParserNode:
    @pytest.mark.asyncio
    async def test_file_parser_success(self, mock_redis):
        """FileParserNode should upload, poll, and return parsed data."""
        mock_client = MagicMock(spec=TongluClient)
        mock_client.upload = AsyncMock(return_value="task-789")
        mock_client.get_task = AsyncMock(return_value={
            "task_id": "task-789",
            "status": "ready",
            "record_id": "rec-789",
        })
        mock_client.get_record = AsyncMock(return_value={
            "id": "rec-789",
            "schema_type": "contract",
            "data": {"party_a": "华为"},
        })

        node = FileParserNode(mock_client)
        bb = TenantBlackboard(mock_redis, "test_tenant")

        result = await node.execute("s1", "test_tenant", {
            "file_path": "/tmp/test.pdf",
            "file_name": "test.pdf",
        }, bb)

        assert result.is_success
        assert result.result["schema_type"] == "contract"
        assert result.artifacts["parsed_data"] is not None

    @pytest.mark.asyncio
    async def test_file_parser_processing_error(self, mock_redis):
        """FileParserNode should handle processing errors."""
        mock_client = MagicMock(spec=TongluClient)
        mock_client.upload = AsyncMock(return_value="task-err")
        mock_client.get_task = AsyncMock(return_value={
            "task_id": "task-err",
            "status": "error",
            "error": "PDF parsing failed",
        })

        node = FileParserNode(mock_client)
        bb = TenantBlackboard(mock_redis, "test_tenant")

        result = await node.execute("s1", "test_tenant", {
            "file_path": "/tmp/bad.pdf",
        }, bb)

        assert result.status == "error"
        assert "文件解析失败" in result.error_message or "文件处理失败" in result.error_message

    @pytest.mark.asyncio
    async def test_file_parser_upload_error(self, mock_redis):
        """FileParserNode should handle upload errors."""
        mock_client = MagicMock(spec=TongluClient)
        mock_client.upload = AsyncMock(side_effect=Exception("Connection refused"))

        node = FileParserNode(mock_client)
        bb = TenantBlackboard(mock_redis, "test_tenant")

        result = await node.execute("s1", "test_tenant", {
            "file_path": "/tmp/test.pdf",
        }, bb)

        assert result.status == "error"

    def test_node_metadata(self):
        """FileParserNode should have correct metadata."""
        node = FileParserNode(MagicMock())
        assert node.node_id == "file_parser"
        assert node.name == "文件解析"
        assert "file_path" in node.param_schema["properties"]
