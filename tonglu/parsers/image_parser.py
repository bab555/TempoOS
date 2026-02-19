# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Image Parser — Extract text from images via DashScope Qwen-VL.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from tonglu.parsers.base import BaseParser, ParseResult

if TYPE_CHECKING:
    from tonglu.services.llm_service import LLMService

logger = logging.getLogger("tonglu.parsers.image")

# Supported image extensions
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp")


class ImageParser(BaseParser):
    """图片解析器 — 通过 Qwen-VL 提取图片中的文字和结构化信息。"""

    def __init__(self, llm_service: "LLMService") -> None:
        self._llm = llm_service

    async def parse(self, content_ref: str, **kwargs: Any) -> ParseResult:
        """
        Parse an image file using Qwen-VL multimodal model.

        Args:
            content_ref: Local file path or URL to the image.
        """
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": content_ref},
                    {
                        "type": "text",
                        "text": (
                            "请提取图片中的所有文字内容和关键信息，"
                            "包括表格、数字、日期、金额等。以纯文本形式输出。"
                        ),
                    },
                ],
            }
        ]

        text = await self._llm.call(task_type="vision", messages=messages)

        logger.debug("Image parsed via VL: %s — %d chars", content_ref, len(text))

        return ParseResult(
            text=text,
            metadata={"source": "qwen-vl", "image_path": content_ref},
        )

    def supports(self, file_name: Optional[str], source_type: str) -> bool:
        if not file_name:
            return False
        return file_name.lower().endswith(IMAGE_EXTENSIONS)
