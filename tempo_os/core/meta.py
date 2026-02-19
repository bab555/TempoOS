# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Agent / Worker Metadata — Declarative definitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class AgentDef:
    """Declarative definition of an agent/worker."""

    name: str
    description: str = ""
    version: str = "1.0"
    capabilities: list[str] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"AgentDef(name={self.name!r}, v={self.version})"
