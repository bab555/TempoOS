# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""Tests for Tonglu configuration."""

import os

import pytest

from tonglu.config import TongluSettings


class TestTongluSettings:
    def test_defaults(self):
        """Settings should have sensible defaults."""
        s = TongluSettings(DASHSCOPE_API_KEY="test-key")
        assert s.HOST == "0.0.0.0"
        assert s.PORT == 8100
        assert s.DASHSCOPE_DEFAULT_MODEL == "qwen-plus"
        assert s.DASHSCOPE_EMBEDDING_MODEL == "text-embedding-v4"
        assert s.INGESTION_MAX_CONCURRENT == 20
        assert s.EVENT_SINK_ENABLED is True

    def test_persist_rules_list(self):
        """persist_rules_list should parse comma-separated string."""
        s = TongluSettings(
            DASHSCOPE_API_KEY="test",
            EVENT_SINK_PERSIST_RULES="contract,invoice,quotation",
        )
        assert s.persist_rules_list == ["contract", "invoice", "quotation"]

    def test_persist_rules_list_with_spaces(self):
        """Should handle spaces in comma-separated values."""
        s = TongluSettings(
            DASHSCOPE_API_KEY="test",
            EVENT_SINK_PERSIST_RULES=" contract , invoice , ",
        )
        assert s.persist_rules_list == ["contract", "invoice"]

    def test_tenant_ids_list(self):
        """tenant_ids_list should parse comma-separated string."""
        s = TongluSettings(
            DASHSCOPE_API_KEY="test",
            EVENT_SINK_TENANT_IDS="tenant_a,tenant_b",
        )
        assert s.tenant_ids_list == ["tenant_a", "tenant_b"]

    def test_extra_fields_ignored(self):
        """Extra env vars (from TempoOS) should be ignored."""
        s = TongluSettings(
            DASHSCOPE_API_KEY="test",
            SOME_RANDOM_VAR="should_not_crash",
        )
        assert s.DASHSCOPE_API_KEY == "test"
