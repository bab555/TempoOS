# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for IdempotencyGuard."""

import pytest
from tempo_os.resilience.idempotency import IdempotencyGuard


class TestIdempotencyGuard:
    @pytest.mark.asyncio
    async def test_first_execution_allowed(self):
        guard = IdempotencyGuard()
        assert await guard.before_execute("s1", "step_a", 1) is True

    @pytest.mark.asyncio
    async def test_duplicate_execution_blocked(self):
        guard = IdempotencyGuard()
        await guard.after_execute("s1", "step_a", 1, "success", {"x": 1})
        assert await guard.before_execute("s1", "step_a", 1) is False

    @pytest.mark.asyncio
    async def test_different_step_allowed(self):
        guard = IdempotencyGuard()
        await guard.after_execute("s1", "step_a", 1, "success")
        assert await guard.before_execute("s1", "step_b", 1) is True

    @pytest.mark.asyncio
    async def test_different_attempt_allowed(self):
        guard = IdempotencyGuard()
        await guard.after_execute("s1", "step_a", 1, "error")
        assert await guard.before_execute("s1", "step_a", 2) is True

    @pytest.mark.asyncio
    async def test_should_retry_under_limit(self):
        guard = IdempotencyGuard()
        await guard.after_execute("s1", "step_a", 1, "error")
        should, next_attempt = await guard.should_retry("s1", "step_a", max_attempts=3)
        assert should is True
        assert next_attempt == 2

    @pytest.mark.asyncio
    async def test_should_not_retry_at_limit(self):
        guard = IdempotencyGuard()
        for i in range(1, 4):
            await guard.after_execute("s1", "step_a", i, "error")
        should, _ = await guard.should_retry("s1", "step_a", max_attempts=3)
        assert should is False
