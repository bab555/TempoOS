# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Data Query Node — Query Tonglu data service from TempoOS workflows.

Builtin node ID: data_query
"""

from __future__ import annotations

from typing import Any, Dict, List

from tempo_os.memory.blackboard import TenantBlackboard
from tempo_os.nodes.base import BaseNode, NodeResult
from tempo_os.runtime.tonglu_client import TongluClient


class DataQueryNode(BaseNode):
    """
    数据查询节点 — 通过铜炉检索 CRM 数据。

    Params:
        intent (str): Natural language query or keyword.
        mode (str): "sql" / "vector" / "hybrid" (default: "hybrid").
        filters (dict): Optional pre-structured filters.
        limit (int): Max results (default: 20).
    """

    node_id = "data_query"
    name = "数据查询"
    description = "通过铜炉检索 CRM 数据（支持语义搜索和精确查询）"
    param_schema = {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "description": "查询意图或关键词"},
            "mode": {"type": "string", "enum": ["sql", "vector", "hybrid"], "default": "hybrid"},
            "filters": {"type": "object", "description": "结构化过滤条件"},
            "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
        },
        "required": ["intent"],
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
            results = await self._tonglu.query(
                intent=params["intent"],
                filters=params.get("filters"),
                tenant_id=tenant_id,
                mode=params.get("mode", "hybrid"),
                limit=params.get("limit", 20),
            )

            result_data = {"records": results, "count": len(results)}

            await blackboard.set_state(session_id, "last_data_query_result", result_data)
            await blackboard.append_result(session_id, "data_query", result_data)

            return NodeResult(
                status="success",
                result=result_data,
                artifacts={"query_result": results},
                ui_schema=self._build_table_schema(results),
            )
        except Exception as e:
            return NodeResult(
                status="error",
                error_message=f"数据查询失败: {e}",
            )

    @staticmethod
    def _build_table_schema(results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Build a UI table schema from query results."""
        if not results:
            return {
                "components": [
                    {"type": "text", "props": {"content": "未找到匹配数据"}}
                ]
            }

        # Extract column keys from first result, excluding internal fields
        columns = [
            {"key": k, "title": k}
            for k in results[0].keys()
            if not k.startswith("_")
        ]

        return {
            "components": [
                {
                    "type": "table",
                    "props": {
                        "columns": columns,
                        "data": results,
                    },
                }
            ]
        }
