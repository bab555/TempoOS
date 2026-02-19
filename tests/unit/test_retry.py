# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for RetryPolicy and RetryManager."""

import pytest
from tempo_os.resilience.retry import RetryPolicy, RetryManager


class TestRetryPolicy:
    def test_first_attempt_delay(self):
        policy = RetryPolicy(backoff_base=1.0, backoff_multiplier=2.0)
        assert policy.next_delay(1) == 1.0

    def test_second_attempt_delay(self):
        policy = RetryPolicy(backoff_base=1.0, backoff_multiplier=2.0)
        assert policy.next_delay(2) == 2.0

    def test_delay_capped(self):
        policy = RetryPolicy(backoff_base=1.0, backoff_multiplier=10.0, max_backoff=5.0)
        assert policy.next_delay(3) == 5.0  # 1*10^2=100, capped to 5


class TestRetryManager:
    @pytest.mark.asyncio
    async def test_should_retry_under_limit(self):
        mgr = RetryManager(RetryPolicy(max_attempts=3))
        assert await mgr.should_retry(1) is True
        assert await mgr.should_retry(2) is True

    @pytest.mark.asyncio
    async def test_should_not_retry_at_limit(self):
        mgr = RetryManager(RetryPolicy(max_attempts=3))
        assert await mgr.should_retry(3) is False

    @pytest.mark.asyncio
    async def test_handle_error_retry(self):
        mgr = RetryManager(RetryPolicy(max_attempts=3))
        result = await mgr.handle_node_error("s1", "step", 1, Exception("fail"))
        assert result == "retry"

    @pytest.mark.asyncio
    async def test_handle_error_dead_letter(self):
        mgr = RetryManager(RetryPolicy(max_attempts=3))
        result = await mgr.handle_node_error("s1", "step", 3, Exception("fail"))
        assert result == "dead_letter"
