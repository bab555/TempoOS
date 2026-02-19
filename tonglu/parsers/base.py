# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Base Parser — Abstract interface for all file/content parsers.

Concrete implementations (PDF, Excel, Image, Text) are in Plan 10.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ParseResult:
    """Parser output — extracted text and optional structured data."""

    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    tables: Optional[List[Any]] = None


class BaseParser(ABC):
    """Abstract base class for all Tonglu parsers."""

    @abstractmethod
    async def parse(self, content_ref: str, **kwargs: Any) -> ParseResult:
        """Parse a file or content reference, returning extracted text."""
        ...

    @abstractmethod
    def supports(self, file_name: Optional[str], source_type: str) -> bool:
        """Check if this parser can handle the given file type."""
        ...
