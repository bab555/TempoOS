# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Retry Policy & Manager — Error handling with exponential backoff.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("tempo.retry")


@dataclass
class RetryPolicy:
    """Configuration for retry behavior."""
    max_attempts: int = 3
    backoff_base: float = 1.0        # seconds
    backoff_multiplier: float = 2.0  # exponential factor
    max_backoff: float = 60.0        # cap

    def next_delay(self, attempt: int) -> float:
        """Calculate delay before next retry (exponential backoff)."""
        delay = self.backoff_base * (self.backoff_multiplier ** (attempt - 1))
        return min(delay, self.max_backoff)


# Default policy
DEFAULT_RETRY_POLICY = RetryPolicy()


class RetryManager:
    """Manages retry logic for failed node executions."""

    def __init__(self, policy: Optional[RetryPolicy] = None):
        self._policy = policy or DEFAULT_RETRY_POLICY

    @property
    def policy(self) -> RetryPolicy:
        return self._policy

    async def should_retry(self, attempt: int) -> bool:
        """Check if we should retry based on attempt count."""
        return attempt < self._policy.max_attempts

    async def wait_before_retry(self, attempt: int) -> None:
        """Wait with exponential backoff before retrying."""
        delay = self._policy.next_delay(attempt)
        logger.info("Retry: waiting %.1fs before attempt %d", delay, attempt + 1)
        await asyncio.sleep(delay)

    async def handle_node_error(
        self,
        session_id: str,
        step: str,
        attempt: int,
        error: Exception,
    ) -> str:
        """
        Decide what to do after a node error.

        Returns: "retry" | "dead_letter" | "abort"
        """
        if attempt < self._policy.max_attempts:
            logger.warning(
                "Node error on %s/%s#%d: %s — will retry",
                session_id, step, attempt, error,
            )
            return "retry"
        else:
            logger.error(
                "Node error on %s/%s#%d: %s — max retries exhausted, dead letter",
                session_id, step, attempt, error,
            )
            return "dead_letter"
