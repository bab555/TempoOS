# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for FanInChecker."""

import pytest
from tempo_os.memory.blackboard import TenantBlackboard
from tempo_os.resilience.fan_in import FanInChecker


class TestFanInChecker:
    @pytest.mark.asyncio
    async def test_all_deps_satisfied(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        checker = FanInChecker(bb)

        await bb.push_artifact("s1", "result_a", {"done": True})
        await bb.push_artifact("s1", "result_b", {"done": True})

        assert await checker.all_deps_done("s1", ["result_a", "result_b"]) is True

    @pytest.mark.asyncio
    async def test_partial_deps_not_satisfied(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        checker = FanInChecker(bb)

        await bb.push_artifact("s1", "result_a", {"done": True})
        # result_b is missing

        assert await checker.all_deps_done("s1", ["result_a", "result_b"]) is False

    @pytest.mark.asyncio
    async def test_no_deps_satisfied(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        checker = FanInChecker(bb)

        assert await checker.all_deps_done("s1", ["result_a"]) is False

    @pytest.mark.asyncio
    async def test_empty_deps_always_true(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        checker = FanInChecker(bb)

        assert await checker.all_deps_done("s1", []) is True

    @pytest.mark.asyncio
    async def test_get_pending_deps(self, mock_redis):
        bb = TenantBlackboard(mock_redis, "test_tenant")
        checker = FanInChecker(bb)

        await bb.push_artifact("s1", "result_a", {"done": True})

        pending = await checker.get_pending_deps("s1", ["result_a", "result_b", "result_c"])
        assert pending == ["result_b", "result_c"]
