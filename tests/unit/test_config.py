# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for TempoSettings configuration."""

from tempo_os.core.config import TempoSettings


class TestTempoSettings:
    def test_defaults(self):
        s = TempoSettings(_env_file=None, DASHSCOPE_API_KEY="test-key")
        assert s.REDIS_URL == "redis://localhost:6379/0"
        assert "postgresql" in s.DATABASE_URL
        assert s.LOG_LEVEL == "INFO"
        assert s.TEMPO_ENV == "dev"
        assert s.SESSION_TTL == 1800
        assert s.MAX_RETRY == 3
        assert s.TICK_INTERVAL == 0.2

    def test_custom_values(self):
        s = TempoSettings(
            _env_file=None,
            REDIS_URL="redis://custom:6380/1",
            DASHSCOPE_API_KEY="sk-test",
            DASHSCOPE_MODEL="qwen-plus",
            SESSION_TTL=3600,
        )
        assert s.REDIS_URL == "redis://custom:6380/1"
        assert s.DASHSCOPE_API_KEY == "sk-test"
        assert s.DASHSCOPE_MODEL == "qwen-plus"
        assert s.SESSION_TTL == 3600
