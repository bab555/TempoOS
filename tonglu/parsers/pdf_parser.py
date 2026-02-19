# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
PDF Parser — Extract text and tables from PDF files.

Uses pdfplumber (synchronous) wrapped in asyncio.to_thread
to avoid blocking the event loop under 20-concurrency.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import pdfplumber

from tonglu.parsers.base import BaseParser, ParseResult

logger = logging.getLogger("tonglu.parsers.pdf")


class PDFParser(BaseParser):
    """PDF 文件解析器。"""

    async def parse(self, content_ref: str, **kwargs: Any) -> ParseResult:
        """
        Parse a PDF file, extracting text and tables.

        Uses asyncio.to_thread because pdfplumber is blocking I/O.
        """
        return await asyncio.to_thread(self._sync_parse, content_ref)

    def supports(self, file_name: Optional[str], source_type: str) -> bool:
        return bool(file_name) and file_name.lower().endswith(".pdf")

    @staticmethod
    def _sync_parse(path: str) -> ParseResult:
        """Synchronous PDF parsing — runs in thread pool."""
        text_parts: list[str] = []
        tables: list[Any] = []

        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
                page_tables = page.extract_tables()
                if page_tables:
                    tables.extend(page_tables)

        logger.debug("PDF parsed: %s — %d pages, %d tables", path, page_count, len(tables))

        return ParseResult(
            text="\n".join(text_parts),
            metadata={"pages": page_count, "has_tables": len(tables) > 0},
            tables=tables if tables else None,
        )
