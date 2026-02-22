# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
TempoOS Configuration — Environment-driven settings.

All configuration is loaded from environment variables (or .env file).
"""

from __future__ import annotations

from typing import Optional

from pydantic_settings import BaseSettings
from pydantic import Field


class TempoSettings(BaseSettings):
    """Platform-wide configuration loaded from environment."""

    # --- Redis ---
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL",
    )

    # --- PostgreSQL ---
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://tempo:password@localhost:5432/tempo_os",
        description="PostgreSQL async connection URL",
    )

    # --- LLM ---
    DASHSCOPE_API_KEY: str = Field(
        default="",
        description="DashScope API key for Qwen models",
    )
    DASHSCOPE_MODEL: str = Field(
        default="qwen3-max",
        description="Default LLM model (central controller)",
    )
    DASHSCOPE_SEARCH_MODEL: str = Field(
        default="qwen-max",
        description="Model for web search (enable_search, text-generation API)",
    )
    DASHSCOPE_VL_MODEL: str = Field(
        default="qwen3.5-plus",
        description="Vision-Language model for image/document understanding",
    )

    # --- OSS Direct Upload (POST Policy) ---
    OSS_ENDPOINT: str = Field(
        default="",
        description="Aliyun OSS endpoint, e.g. oss-cn-hangzhou.aliyuncs.com",
    )
    OSS_BUCKET: str = Field(
        default="",
        description="Aliyun OSS bucket name",
    )
    OSS_ACCESS_KEY_ID: str = Field(
        default="",
        description="Aliyun AccessKeyId (server-side only, never expose to frontend)",
    )
    OSS_ACCESS_KEY_SECRET: str = Field(
        default="",
        description="Aliyun AccessKeySecret (server-side only, never expose to frontend)",
    )
    OSS_UPLOAD_PREFIX: str = Field(
        default="tempoos",
        description="Base prefix for uploads in OSS key path",
    )
    OSS_MAX_UPLOAD_SIZE: int = Field(
        default=200 * 1024 * 1024,
        description="Max upload size in bytes for direct upload policy",
    )

    # --- Platform ---
    LOG_LEVEL: str = Field(default="INFO")
    TEMPO_ENV: str = Field(
        default="dev",
        description="Environment: dev | prod",
    )
    SESSION_TTL: int = Field(
        default=1800,
        description="Default session TTL in seconds (30 min)",
    )
    CHAT_HISTORY_TTL: int = Field(
        default=86400,
        description="Chat history TTL in seconds (24h, longer than session for review)",
    )
    LLM_CONTEXT_MAX_ROUNDS: int = Field(
        default=6,
        description="Max recent conversation rounds to keep in full for LLM context",
    )
    LLM_CONTEXT_SUMMARY_THRESHOLD: int = Field(
        default=10,
        description="Number of messages beyond which early history is summarized",
    )
    DASHSCOPE_SUMMARY_MODEL: str = Field(
        default="qwen3.5-plus",
        description="Lightweight model for chat history summarization",
    )
    TONGLU_BASE_URL: str = Field(
        default="http://127.0.0.1:8100",
        description="Tonglu service base URL for internal API calls",
    )
    MAX_RETRY: int = Field(
        default=3,
        description="Max retry attempts for node execution",
    )
    TICK_INTERVAL: float = Field(
        default=0.2,
        description="TempoClock tick interval in seconds",
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


# Global singleton
settings = TempoSettings()
