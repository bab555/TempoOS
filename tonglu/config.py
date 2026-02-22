# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Tonglu Configuration — Environment-driven settings.

All configuration is loaded from environment variables (or .env file).
Uses TONGLU_ prefix to avoid collision with TempoOS settings.
"""

from __future__ import annotations

from typing import List

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


class TongluSettings(BaseSettings):
    """铜炉服务配置，从环境变量加载。"""

    # ── 服务 ──────────────────────────────────────────────────
    HOST: str = Field(default="0.0.0.0", description="Bind host")
    PORT: int = Field(default=8100, description="Bind port")

    # ── 数据库（与 TempoOS 共享 PG 实例）──────────────────────
    # 优先读 TONGLU_DATABASE_URL，其次读共享的 DATABASE_URL
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://tempo:password@127.0.0.1:5432/tempo_os",
        description="PostgreSQL async connection URL",
        validation_alias=AliasChoices("TONGLU_DATABASE_URL", "DATABASE_URL"),
    )

    # ── Redis（与 TempoOS 共享）────────────────────────────────
    # 优先读 TONGLU_REDIS_URL，其次读共享的 REDIS_URL
    REDIS_URL: str = Field(
        default="redis://127.0.0.1:6379/0",
        description="Redis connection URL (shared with TempoOS)",
        validation_alias=AliasChoices("TONGLU_REDIS_URL", "REDIS_URL"),
    )

    # ── DashScope LLM ─────────────────────────────────────────
    # 优先读 TONGLU_DASHSCOPE_API_KEY，其次读共享的 DASHSCOPE_API_KEY
    DASHSCOPE_API_KEY: str = Field(
        default="",
        description="DashScope API key for Qwen models",
        validation_alias=AliasChoices("TONGLU_DASHSCOPE_API_KEY", "DASHSCOPE_API_KEY"),
    )
    DASHSCOPE_DEFAULT_MODEL: str = Field(
        default="qwen3-max",
        description="Default LLM model for extraction/summarization",
    )
    DASHSCOPE_EMBEDDING_MODEL: str = Field(
        default="text-embedding-v4",
        description="Embedding model name",
    )
    DASHSCOPE_VL_MODEL: str = Field(
        default="qwen3.5-plus",
        description="Vision-Language model for image/document understanding",
    )

    # ── 处理 ──────────────────────────────────────────────────
    INGESTION_MAX_CONCURRENT: int = Field(
        default=20,
        description="Max concurrent file processing tasks",
    )
    INGESTION_TIMEOUT_SECONDS: int = Field(
        default=120,
        description="Per-file processing timeout in seconds",
    )

    # ── Event Sink ────────────────────────────────────────────
    EVENT_SINK_ENABLED: bool = Field(
        default=True,
        description="Enable automatic Blackboard artifact persistence",
    )
    EVENT_SINK_TENANT_IDS: str = Field(
        default="default",
        description="Comma-separated tenant IDs to monitor",
    )
    EVENT_SINK_PERSIST_RULES: str = Field(
        default="sourcing_result,quotation,contract_draft,finance_report,equipment_list,document_final",
        description="Comma-separated artifact keys to persist",
    )

    # ── Session Evictor (Redis ↔ PG cold swap) ────────────────
    SESSION_EVICTOR_ENABLED: bool = Field(
        default=True,
        description="Enable periodic session archival from Redis to PG",
    )
    SESSION_EVICTOR_SCAN_INTERVAL: int = Field(
        default=300,
        description="Scan interval in seconds (default 5 min)",
    )
    SESSION_EVICTOR_TTL_THRESHOLD: int = Field(
        default=300,
        description="Archive sessions with remaining TTL below this (seconds)",
    )

    # ── Helpers ───────────────────────────────────────────────

    @property
    def persist_rules_list(self) -> List[str]:
        """Parse comma-separated persist rules into a list."""
        return [r.strip() for r in self.EVENT_SINK_PERSIST_RULES.split(",") if r.strip()]

    @property
    def tenant_ids_list(self) -> List[str]:
        """Parse comma-separated tenant IDs into a list."""
        return [t.strip() for t in self.EVENT_SINK_TENANT_IDS.split(",") if t.strip()]

    model_config = {
        "env_prefix": "TONGLU_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore",  # Ignore TempoOS vars in shared .env
    }


_settings_singleton: TongluSettings | None = None


def get_settings() -> TongluSettings:
    """Return a cached TongluSettings singleton."""
    global _settings_singleton
    if _settings_singleton is None:
        _settings_singleton = TongluSettings()
    return _settings_singleton
