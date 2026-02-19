# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
LLM Service — Unified DashScope wrapper with task-type routing.

Phase 1: All calls go to DashScope commercial API.
Phase 2: task_type → model_tier → concrete model instance (Ollama / DashScope).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import dashscope

logger = logging.getLogger("tonglu.llm")


class LLMService:
    """
    DashScope 统一封装。

    通过 task_type 选择模型，为 Phase 2 模型分层预留接口。
    """

    # Phase 1: task_type → DashScope model name
    MODEL_MAP: Dict[str, str] = {
        "route": "qwen3-max",         # 类型识别
        "extract": "qwen3-max",       # 字段提取
        "summarize": "qwen3-max",     # 摘要生成
        "validate": "qwen3-max",      # 疑难数据 fallback
        "vision": "qwen3.5-plus",     # 图片/文档理解 (VL)
    }

    MAX_RETRIES = 3

    def __init__(
        self,
        api_key: str,
        default_model: str = "qwen-plus",
        embedding_model: str = "text-embedding-v3",
    ) -> None:
        self.api_key = api_key
        self.default_model = default_model
        self.embedding_model = embedding_model

    # ── Chat / Generation ─────────────────────────────────────

    async def call(
        self,
        task_type: str,
        messages: List[Dict[str, Any]],
        **kwargs: Any,
    ) -> str:
        """
        统一 LLM 调用入口。

        Args:
            task_type: 任务类型，用于选择模型。
                Phase 1: 直接映射到 DashScope model name。
                Phase 2: 可扩展为 task_type → model_tier → 具体模型实例。
            messages: OpenAI-style message list.
            **kwargs: Extra params forwarded to DashScope.

        Returns:
            LLM response content string.

        Raises:
            RuntimeError: After MAX_RETRIES failures.
        """
        model = self.MODEL_MAP.get(task_type, self.default_model)

        last_error: Optional[Exception] = None
        for attempt in range(self.MAX_RETRIES):
            try:
                response = await self._async_call(model, messages, **kwargs)
                return response
            except Exception as e:
                last_error = e
                if attempt < self.MAX_RETRIES - 1:
                    wait = 2 ** attempt
                    logger.warning(
                        "LLM call failed (attempt %d/%d, model=%s): %s. "
                        "Retrying in %ds...",
                        attempt + 1, self.MAX_RETRIES, model, e, wait,
                    )
                    await asyncio.sleep(wait)

        raise RuntimeError(
            f"LLM call failed after {self.MAX_RETRIES} attempts "
            f"(model={model}, task_type={task_type}): {last_error}"
        ) from last_error

    # ── Embedding ─────────────────────────────────────────────

    async def embed(
        self,
        texts: List[str],
        model: Optional[str] = None,
    ) -> List[List[float]]:
        """
        文本向量化。

        Uses asyncio.to_thread to avoid blocking the event loop,
        since dashscope.TextEmbedding.call() is synchronous.
        """
        model = model or self.embedding_model

        def _sync_embed() -> List[List[float]]:
            response = dashscope.TextEmbedding.call(
                model=model,
                input=texts,
                api_key=self.api_key,
            )
            if response.status_code != 200:
                raise RuntimeError(
                    f"Embedding error: {response.code} - {response.message}"
                )
            return [
                item["embedding"]
                for item in response.output["embeddings"]
            ]

        return await asyncio.to_thread(_sync_embed)

    # ── Internal ──────────────────────────────────────────────

    async def _async_call(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        **kwargs: Any,
    ) -> str:
        """
        Async wrapper around DashScope's synchronous Generation.call().

        Uses asyncio.to_thread to avoid blocking the event loop.
        """
        def _sync_call() -> str:
            response = dashscope.Generation.call(
                model=model,
                messages=messages,
                api_key=self.api_key,
                result_format="message",
                **kwargs,
            )
            if response.status_code != 200:
                raise RuntimeError(
                    f"DashScope error: {response.code} - {response.message}"
                )
            return response.output.choices[0].message.content

        return await asyncio.to_thread(_sync_call)
