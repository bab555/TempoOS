# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Model Gateway API — LLM chat and embedding endpoints.

Wired to DashScope SDK for real inference.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from tempo_os.api.deps import get_current_tenant
from tempo_os.core.config import settings
from tempo_os.core.tenant import TenantContext

logger = logging.getLogger("tempo.api.gateway")

router = APIRouter(prefix="/llm", tags=["llm"])

_GW_MAX_RETRIES = 3


class ChatRequest(BaseModel):
    messages: List[Dict[str, str]]
    model: str = "qwen-max"
    temperature: float = 0.7
    stream: bool = False

class ChatResponse(BaseModel):
    content: str
    model: str
    usage: Optional[Dict[str, int]] = None

class EmbeddingRequest(BaseModel):
    texts: List[str]
    model: str = "text-embedding-v4"

class EmbeddingResponse(BaseModel):
    vectors: List[List[float]]
    dim: int


@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Call DashScope Generation API for chat completion."""
    if not settings.DASHSCOPE_API_KEY:
        raise HTTPException(503, "LLM service not configured (missing DASHSCOPE_API_KEY)")

    def _sync_call():
        import dashscope
        response = dashscope.Generation.call(
            model=req.model,
            messages=req.messages,
            api_key=settings.DASHSCOPE_API_KEY,
            result_format="message",
            temperature=req.temperature,
        )
        if response.status_code != 200:
            raise RuntimeError(f"DashScope error: {response.code} - {response.message}")
        msg = response.output.choices[0].message
        try:
            content = (msg["content"] if "content" in msg else "") or ""
        except (TypeError, KeyError):
            content = getattr(msg, "content", "") or ""

        usage = None
        try:
            resp_usage = response.usage
            if resp_usage is not None:
                if isinstance(resp_usage, dict):
                    usage = {
                        "input_tokens": resp_usage.get("input_tokens", 0),
                        "output_tokens": resp_usage.get("output_tokens", 0),
                    }
                else:
                    usage = {
                        "input_tokens": getattr(resp_usage, "input_tokens", 0),
                        "output_tokens": getattr(resp_usage, "output_tokens", 0),
                    }
        except Exception:
            pass
        return content, usage

    last_error = None
    for attempt in range(_GW_MAX_RETRIES):
        try:
            content, usage = await asyncio.to_thread(_sync_call)
            return ChatResponse(content=content, model=req.model, usage=usage)
        except Exception as e:
            last_error = e
            if attempt < _GW_MAX_RETRIES - 1:
                wait = 2 ** attempt
                logger.warning("Gateway chat failed (attempt %d/%d): %s. Retrying in %ds...",
                               attempt + 1, _GW_MAX_RETRIES, e, wait)
                await asyncio.sleep(wait)

    logger.error("Gateway chat failed after %d attempts: %s", _GW_MAX_RETRIES, last_error)
    raise HTTPException(502, f"LLM call failed after {_GW_MAX_RETRIES} retries: {last_error}")


@router.post("/embedding", response_model=EmbeddingResponse)
async def embedding(
    req: EmbeddingRequest,
    tenant: TenantContext = Depends(get_current_tenant),
):
    """Generate text embeddings via DashScope TextEmbedding API."""
    if not settings.DASHSCOPE_API_KEY:
        raise HTTPException(503, "LLM service not configured (missing DASHSCOPE_API_KEY)")

    def _sync_embed():
        import dashscope
        response = dashscope.TextEmbedding.call(
            model=req.model,
            input=req.texts,
            api_key=settings.DASHSCOPE_API_KEY,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Embedding error: {response.code} - {response.message}")
        embeddings = [item["embedding"] for item in response.output["embeddings"]]
        dim = len(embeddings[0]) if embeddings else 0
        return embeddings, dim

    last_error = None
    for attempt in range(_GW_MAX_RETRIES):
        try:
            vectors, dim = await asyncio.to_thread(_sync_embed)
            return EmbeddingResponse(vectors=vectors, dim=dim)
        except Exception as e:
            last_error = e
            if attempt < _GW_MAX_RETRIES - 1:
                wait = 2 ** attempt
                logger.warning("Gateway embedding failed (attempt %d/%d): %s. Retrying in %ds...",
                               attempt + 1, _GW_MAX_RETRIES, e, wait)
                await asyncio.sleep(wait)

    logger.error("Gateway embedding failed after %d attempts: %s", _GW_MAX_RETRIES, last_error)
    raise HTTPException(502, f"Embedding call failed after {_GW_MAX_RETRIES} retries: {last_error}")
