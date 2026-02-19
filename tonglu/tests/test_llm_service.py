# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""Tests for LLMService — DashScope wrapper."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tonglu.services.llm_service import LLMService


class TestLLMServiceModelMap:
    def test_model_map_keys(self):
        """MODEL_MAP should contain all expected task types."""
        expected = {"route", "extract", "summarize", "validate", "vision"}
        assert set(LLMService.MODEL_MAP.keys()) == expected

    def test_route_uses_turbo(self):
        assert LLMService.MODEL_MAP["route"] == "qwen-turbo"

    def test_extract_uses_plus(self):
        assert LLMService.MODEL_MAP["extract"] == "qwen-plus"

    def test_validate_uses_max(self):
        assert LLMService.MODEL_MAP["validate"] == "qwen-max"

    def test_vision_uses_vl_max(self):
        assert LLMService.MODEL_MAP["vision"] == "qwen-vl-max"


class TestLLMServiceCall:
    @pytest.mark.asyncio
    async def test_call_success(self):
        """call() should return content on success."""
        svc = LLMService(api_key="test-key")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.output.choices = [
            MagicMock(message=MagicMock(content="extracted data"))
        ]

        with patch("dashscope.Generation.call", return_value=mock_response):
            result = await svc.call(
                task_type="extract",
                messages=[{"role": "user", "content": "test"}],
            )
        assert result == "extracted data"

    @pytest.mark.asyncio
    async def test_call_selects_correct_model(self):
        """call() should select model based on task_type."""
        svc = LLMService(api_key="test-key")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.output.choices = [
            MagicMock(message=MagicMock(content="ok"))
        ]

        with patch("dashscope.Generation.call", return_value=mock_response) as mock_gen:
            await svc.call(task_type="route", messages=[{"role": "user", "content": "test"}])
            # Verify the model passed to Generation.call
            call_kwargs = mock_gen.call_args
            assert call_kwargs.kwargs.get("model") == "qwen-turbo" or \
                   call_kwargs[1].get("model") == "qwen-turbo"

    @pytest.mark.asyncio
    async def test_call_unknown_task_uses_default(self):
        """Unknown task_type should fall back to default model."""
        svc = LLMService(api_key="test-key", default_model="qwen-plus")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.output.choices = [
            MagicMock(message=MagicMock(content="ok"))
        ]

        with patch("dashscope.Generation.call", return_value=mock_response) as mock_gen:
            await svc.call(task_type="unknown_task", messages=[{"role": "user", "content": "test"}])
            call_kwargs = mock_gen.call_args
            assert call_kwargs.kwargs.get("model") == "qwen-plus" or \
                   call_kwargs[1].get("model") == "qwen-plus"

    @pytest.mark.asyncio
    async def test_call_retries_on_failure(self):
        """call() should retry on failure up to MAX_RETRIES."""
        svc = LLMService(api_key="test-key")
        svc.MAX_RETRIES = 2

        fail_response = MagicMock()
        fail_response.status_code = 500
        fail_response.code = "InternalError"
        fail_response.message = "Server error"

        with patch("dashscope.Generation.call", return_value=fail_response):
            with pytest.raises(RuntimeError, match="LLM call failed after 2 attempts"):
                await svc.call(
                    task_type="extract",
                    messages=[{"role": "user", "content": "test"}],
                )


class TestLLMServiceEmbed:
    @pytest.mark.asyncio
    async def test_embed_success(self):
        """embed() should return embedding vectors."""
        svc = LLMService(api_key="test-key")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.output = {
            "embeddings": [
                {"embedding": [0.1, 0.2, 0.3]},
                {"embedding": [0.4, 0.5, 0.6]},
            ]
        }

        with patch("dashscope.TextEmbedding.call", return_value=mock_response):
            result = await svc.embed(["text1", "text2"])

        assert len(result) == 2
        assert result[0] == [0.1, 0.2, 0.3]
        assert result[1] == [0.4, 0.5, 0.6]

    @pytest.mark.asyncio
    async def test_embed_error(self):
        """embed() should raise on API error."""
        svc = LLMService(api_key="test-key")

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.code = "InvalidParam"
        mock_response.message = "Bad input"

        with patch("dashscope.TextEmbedding.call", return_value=mock_response):
            with pytest.raises(RuntimeError, match="Embedding error"):
                await svc.embed(["test"])
