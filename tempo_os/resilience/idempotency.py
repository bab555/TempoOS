# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Idempotency Guard — At-least-once + idempotent node execution.

Wraps node execution to ensure:
  - Before: check if this (session, step, attempt) already ran
  - After: record the result
  - Retry: compute next attempt number
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("tempo.idempotency")


class IdempotencyGuard:
    """
    Ensures idempotent node execution using a storage backend.

    Storage can be PG IdempotencyRepository or an in-memory dict (for testing).
    """

    def __init__(self, storage=None):
        """
        Args:
            storage: Object with check/record/get_max_attempt methods.
                     If None, uses in-memory storage (for testing).
        """
        self._storage = storage or InMemoryIdempotencyStore()

    async def before_execute(
        self, session_id: str, step: str, attempt: int = 1
    ) -> bool:
        """
        Check if this execution has already been recorded.

        Returns True if execution should proceed, False if already done.
        """
        already_done = await self._storage.check(session_id, step, attempt)
        if already_done:
            logger.info(
                "Idempotency: skipping %s/%s#%d (already executed)",
                session_id, step, attempt,
            )
            return False
        return True

    async def after_execute(
        self,
        session_id: str,
        step: str,
        attempt: int,
        status: str,
        result: Optional[Dict] = None,
    ) -> None:
        """Record the execution result."""
        result_hash = None
        if result:
            result_hash = hashlib.sha256(
                json.dumps(result, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()[:16]

        await self._storage.record(session_id, step, attempt, status, result_hash)
        logger.info(
            "Idempotency: recorded %s/%s#%d status=%s",
            session_id, step, attempt, status,
        )

    async def should_retry(
        self, session_id: str, step: str, max_attempts: int = 3
    ) -> Tuple[bool, int]:
        """
        Check if we should retry this step.

        Returns (should_retry, next_attempt_number).
        """
        max_attempt = await self._storage.get_max_attempt(session_id, step)
        if max_attempt >= max_attempts:
            return False, max_attempt
        return True, max_attempt + 1


class InMemoryIdempotencyStore:
    """In-memory idempotency store for testing."""

    def __init__(self):
        self._records: Dict[Tuple[str, str, int], Dict] = {}

    async def check(self, session_id: str, step: str, attempt: int) -> bool:
        return (session_id, step, attempt) in self._records

    async def record(
        self, session_id: str, step: str, attempt: int,
        status: str, result_hash: Optional[str] = None
    ) -> None:
        self._records[(session_id, step, attempt)] = {
            "status": status,
            "result_hash": result_hash,
        }

    async def get_max_attempt(self, session_id: str, step: str) -> int:
        max_a = 0
        for (sid, s, a) in self._records:
            if sid == session_id and s == step:
                max_a = max(max_a, a)
        return max_a
