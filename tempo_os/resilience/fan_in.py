# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Fan-in Checker — Parallel node convergence.

Checks whether all prerequisite steps have completed before
allowing a merge/convergence step to proceed.

Uses Blackboard artifacts and/or event records to determine completion.
"""

from __future__ import annotations

import logging
from typing import List

from tempo_os.memory.blackboard import TenantBlackboard

logger = logging.getLogger("tempo.fan_in")


class FanInChecker:
    """
    Checks if all parallel dependencies are satisfied.

    Dependencies are identified by artifact keys in the Blackboard.
    A step is "done" if its corresponding artifact exists.
    """

    def __init__(self, blackboard: TenantBlackboard) -> None:
        self._blackboard = blackboard

    async def all_deps_done(
        self,
        session_id: str,
        required_artifact_keys: List[str],
    ) -> bool:
        """
        Check if all required artifacts exist in the Blackboard.

        Args:
            session_id: Current session
            required_artifact_keys: List of artifact keys that must exist

        Returns:
            True if all dependencies are satisfied
        """
        for key in required_artifact_keys:
            artifact = await self._blackboard.get_artifact(key)
            if artifact is None:
                logger.debug(
                    "Fan-in: dependency '%s' not satisfied (session=%s)",
                    key, session_id,
                )
                return False
        logger.info(
            "Fan-in: all %d dependencies satisfied (session=%s)",
            len(required_artifact_keys), session_id,
        )
        return True

    async def get_pending_deps(
        self,
        session_id: str,
        required_artifact_keys: List[str],
    ) -> List[str]:
        """Return list of artifact keys that are NOT yet available."""
        pending = []
        for key in required_artifact_keys:
            artifact = await self._blackboard.get_artifact(key)
            if artifact is None:
                pending.append(key)
        return pending
