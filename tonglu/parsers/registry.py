# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Parser Registry — Select the appropriate parser based on file type.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from tonglu.parsers.base import BaseParser
from tonglu.parsers.excel_parser import ExcelParser
from tonglu.parsers.image_parser import ImageParser
from tonglu.parsers.pdf_parser import PDFParser
from tonglu.parsers.text_parser import TextParser

if TYPE_CHECKING:
    from tonglu.services.llm_service import LLMService

logger = logging.getLogger("tonglu.parsers.registry")


class ParserRegistry:
    """
    根据文件类型选择解析器。

    优先级：PDF > Excel > Image > Text（兜底）
    """

    def __init__(self, llm_service: "LLMService") -> None:
        self._parsers: list[BaseParser] = [
            PDFParser(),
            ExcelParser(),
            ImageParser(llm_service),
        ]
        self._fallback = TextParser()

    def get_parser(self, file_name: Optional[str], source_type: str) -> BaseParser:
        """
        Select the best parser for the given file.

        Returns TextParser as fallback if no specialized parser matches.
        """
        for parser in self._parsers:
            if parser.supports(file_name, source_type):
                logger.debug(
                    "Selected parser %s for file=%s type=%s",
                    parser.__class__.__name__, file_name, source_type,
                )
                return parser

        logger.debug(
            "No specialized parser for file=%s type=%s, using TextParser",
            file_name, source_type,
        )
        return self._fallback
