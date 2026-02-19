# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Text Parser — Fallback parser for plain text and event data.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from tonglu.parsers.base import BaseParser, ParseResult

logger = logging.getLogger("tonglu.parsers.text")


class TextParser(BaseParser):
    """
    兜底解析器 — 直接将 content_ref 作为文本返回。

    适用于：
    - source_type = "text"（直接文本输入）
    - source_type = "event"（Event Sink 的 Blackboard artifact）
    - 无法匹配其他解析器的情况
    """

    async def parse(self, content_ref: str, **kwargs: Any) -> ParseResult:
        logger.debug("Text parser: %d chars", len(content_ref))
        return ParseResult(
            text=content_ref,
            metadata={"parser": "text"},
        )

    def supports(self, file_name: Optional[str], source_type: str) -> bool:
        return source_type in ("text", "event") or not file_name
