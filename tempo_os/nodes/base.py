# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
BaseNode — Abstract base class for all built-in nodes.

Every platform node implements execute() and returns a NodeResult.
Nodes read/write data via Blackboard, not via parameter passing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from tempo_os.memory.blackboard import TenantBlackboard


@dataclass
class NodeResult:
    """Result returned by every node execution."""

    status: str  # "success" | "error" | "need_user_input"
    result: Dict[str, Any] = field(default_factory=dict)
    ui_schema: Optional[Dict[str, Any]] = None
    artifacts: Dict[str, Any] = field(default_factory=dict)
    next_events: List[str] = field(default_factory=list)
    error_message: Optional[str] = None

    @property
    def is_success(self) -> bool:
        return self.status == "success"

    @property
    def needs_user_input(self) -> bool:
        return self.status == "need_user_input"


class BaseNode(ABC):
    """
    Abstract base class for all built-in nodes.

    Subclasses must set class-level attributes and implement execute().
    """

    node_id: str = ""
    name: str = ""
    description: str = ""
    param_schema: Dict[str, Any] = {}

    @abstractmethod
    async def execute(
        self,
        session_id: str,
        tenant_id: str,
        params: Dict[str, Any],
        blackboard: TenantBlackboard,
    ) -> NodeResult:
        """
        Execute the node logic.

        Data flow:
          - params: lightweight step parameters from the engine
          - blackboard: read previous artifacts, write new ones

        Returns:
          NodeResult with status, result data, ui_schema, artifacts
        """
        ...

    def get_info(self) -> Dict[str, Any]:
        """Return node metadata for registry listing."""
        return {
            "node_id": self.node_id,
            "name": self.name,
            "description": self.description,
            "param_schema": self.param_schema,
        }
