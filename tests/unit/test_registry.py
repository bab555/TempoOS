# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for WorkerRegistry."""

import pytest
from tempo_os.kernel.registry import WorkerRegistry


class DummyWorker:
    pass


class TestWorkerRegistry:
    def test_register_and_get(self):
        reg = WorkerRegistry()
        reg.register("echo", DummyWorker)
        assert reg.get("echo") is DummyWorker

    def test_get_missing_returns_none(self):
        reg = WorkerRegistry()
        assert reg.get("nonexistent") is None

    def test_list_names(self):
        reg = WorkerRegistry()
        reg.register("a", DummyWorker)
        reg.register("b", DummyWorker)
        assert sorted(reg.list_names()) == ["a", "b"]

    def test_contains(self):
        reg = WorkerRegistry()
        reg.register("echo", DummyWorker)
        assert "echo" in reg
        assert "nope" not in reg

    def test_len(self):
        reg = WorkerRegistry()
        assert len(reg) == 0
        reg.register("echo", DummyWorker)
        assert len(reg) == 1

    def test_resolve_dotted_path(self):
        reg = WorkerRegistry()
        cls = reg.resolve("tempo_os.workers.std.echo.EchoWorker")
        from tempo_os.workers.std.echo import EchoWorker
        assert cls is EchoWorker
