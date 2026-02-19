# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Data Ingest Node — Write data to Tonglu from TempoOS workflows.

Builtin node ID: data_ingest
"""

from __future__ import annotations

from typing import Any, Dict

from tempo_os.memory.blackboard import TenantBlackboard
from tempo_os.nodes.base import BaseNode, NodeResult
from tempo_os.runtime.tonglu_client import TongluClient


class DataIngestNode(BaseNode):
    """
    数据写入节点 — 将工作流产物写入铜炉。

    Params:
        data (dict): Data to ingest (直接传入).
        artifact_key (str): Or read data from Blackboard artifact.
        schema_type (str): Optional data type hint.
    """

    node_id = "data_ingest"
    name = "数据写入"
    description = "将工作流数据写入铜炉持久化存储"
    param_schema = {
        "type": "object",
        "properties": {
            "data": {"type": "object", "description": "要写入的数据"},
            "artifact_key": {"type": "string", "description": "从 Blackboard 读取的 artifact key"},
            "schema_type": {"type": "string", "description": "数据类型提示"},
        },
    }

    def __init__(self, tonglu_client: TongluClient) -> None:
        self._tonglu = tonglu_client

    async def execute(
        self,
        session_id: str,
        tenant_id: str,
        params: Dict[str, Any],
        blackboard: TenantBlackboard,
    ) -> NodeResult:
        try:
            # Get data from params or Blackboard
            data = params.get("data")
            if not data and params.get("artifact_key"):
                artifact = await blackboard.get_artifact(params["artifact_key"])
                if artifact:
                    data = artifact
                else:
                    return NodeResult(
                        status="error",
                        error_message=f"Artifact '{params['artifact_key']}' not found in Blackboard",
                    )

            if not data:
                return NodeResult(
                    status="error",
                    error_message="No data provided: set 'data' or 'artifact_key' in params",
                )

            record_id = await self._tonglu.ingest(
                data=data,
                tenant_id=tenant_id,
                schema_type=params.get("schema_type"),
            )

            return NodeResult(
                status="success",
                result={"record_id": record_id},
            )
        except Exception as e:
            return NodeResult(
                status="error",
                error_message=f"数据写入失败: {e}",
            )
