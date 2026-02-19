# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""Transform Node — Data transformation (extract, template, format)."""

import json
from tempo_os.nodes.base import BaseNode, NodeResult
from typing import Any, Dict


class TransformNode(BaseNode):
    node_id = "transform"
    name = "Data Transform"
    description = "Extract, reshape, or format data from Blackboard artifacts"
    param_schema = {
        "source_artifact": "Artifact key to read from Blackboard",
        "extract_path": "Dot-separated path to extract (e.g. 'items.0.name')",
        "output_key": "Artifact key to write result to",
    }

    async def execute(self, session_id, tenant_id, params, blackboard):
        source_key = params.get("source_artifact", "")
        extract_path = params.get("extract_path", "")
        output_key = params.get("output_key", "transform_result")

        # Read source
        data = await blackboard.get_artifact(source_key)
        if data is None:
            data = await blackboard.get_state(session_id, source_key)

        if data is None:
            return NodeResult(
                status="error",
                error_message=f"Source '{source_key}' not found",
            )

        # Extract by path
        result = data
        if extract_path:
            for part in extract_path.split("."):
                if isinstance(result, dict):
                    result = result.get(part)
                elif isinstance(result, list):
                    try:
                        result = result[int(part)]
                    except (ValueError, IndexError):
                        result = None
                else:
                    result = None
                if result is None:
                    break

        # Write output
        await blackboard.push_artifact(session_id, output_key, {"value": result})

        return NodeResult(
            status="success",
            result={"extracted": result},
            artifacts={output_key: result},
        )
