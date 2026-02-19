# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Worker Registry — Dynamic Worker Management.

Workers register themselves here; the Bootloader uses this to
instantiate and start workers from configuration.
"""

from __future__ import annotations

import importlib
import logging
from typing import Dict, Optional, Type

logger = logging.getLogger("tempo.registry")


class WorkerRegistry:
    """
    Central registry for all available Worker classes.

    Workers can be registered explicitly or auto-discovered from
    a dotted Python path in the boot config.
    """

    def __init__(self) -> None:
        self._workers: Dict[str, Type] = {}

    def register(self, name: str, worker_class: Type) -> None:
        """Register a worker class by name."""
        self._workers[name] = worker_class
        logger.info("Registered worker: %s -> %s", name, worker_class.__name__)

    def get(self, name: str) -> Optional[Type]:
        """Retrieve a worker class by name."""
        return self._workers.get(name)

    def resolve(self, dotted_path: str) -> Type:
        """
        Import and return a worker class from a dotted Python path.

        Example: 'tempo_os.workers.std.echo.EchoWorker'
        """
        module_path, _, class_name = dotted_path.rpartition(".")
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        return cls

    def list_names(self) -> list[str]:
        return list(self._workers.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._workers

    def __len__(self) -> int:
        return len(self._workers)


# Global singleton
default_worker_registry = WorkerRegistry()
