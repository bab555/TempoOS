# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
File Parser Node — Upload and parse files via Tonglu from TempoOS workflows.

Builtin node ID: file_parser
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from tempo_os.memory.blackboard import TenantBlackboard
from tempo_os.nodes.base import BaseNode, NodeResult
from tempo_os.runtime.tonglu_client import TongluClient

logger = logging.getLogger("tempo.nodes.file_parser")


class FileParserNode(BaseNode):
    """
    文件解析节点 — 上传文件到铜炉并等待解析结果。

    Params:
        file_path (str): Local file path to upload.
        file_name (str): Optional display name for the file.
        schema_type (str): Optional data type hint.
        timeout (int): Max wait time in seconds (default: 120).
    """

    node_id = "file_parser"
    name = "文件解析"
    description = "上传文件到铜炉进行智能解析（PDF/Excel/图片）"
    param_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "文件路径"},
            "file_name": {"type": "string", "description": "文件名"},
            "schema_type": {"type": "string", "description": "数据类型提示"},
            "timeout": {"type": "integer", "default": 120, "description": "超时时间（秒）"},
        },
        "required": ["file_path"],
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
            file_path = params["file_path"]
            file_name = params.get("file_name", "")
            timeout = params.get("timeout", 120)

            # Step 1: Upload file to Tonglu
            task_id = await self._tonglu.upload(
                file_path=file_path,
                file_name=file_name,
                tenant_id=tenant_id,
                schema_type=params.get("schema_type"),
            )

            logger.info(
                "File uploaded to Tonglu: task_id=%s file=%s",
                task_id, file_name or file_path,
            )

            # Step 2: Poll for result
            record = await self._wait_for_result(task_id, timeout=timeout)

            return NodeResult(
                status="success",
                result=record,
                artifacts={"parsed_data": record},
            )
        except TimeoutError as e:
            return NodeResult(
                status="error",
                error_message=str(e),
            )
        except Exception as e:
            return NodeResult(
                status="error",
                error_message=f"文件解析失败: {e}",
            )

    async def _wait_for_result(self, task_id: str, timeout: int = 120) -> Dict[str, Any]:
        """Poll Tonglu task status until complete or timeout."""
        deadline = asyncio.get_event_loop().time() + timeout
        poll_interval = 2  # seconds

        while asyncio.get_event_loop().time() < deadline:
            task = await self._tonglu.get_task(task_id)

            if task["status"] == "ready":
                record_id = task.get("record_id")
                if record_id:
                    return await self._tonglu.get_record(record_id)
                return task

            elif task["status"] == "error":
                raise RuntimeError(f"文件处理失败: {task.get('error')}")

            await asyncio.sleep(poll_interval)

        raise TimeoutError(f"文件处理超时 ({timeout}s), task_id={task_id}")
