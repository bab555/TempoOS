# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Echo Worker — Standard reference worker for testing.

Simply echoes back whatever it receives. Used to verify the
entire event bus pipeline without business logic.
"""

from __future__ import annotations

import logging
from typing import Optional

from tempo_os.workers.base import BaseWorker
from tempo_os.protocols.schema import TempoEvent

logger = logging.getLogger("tempo.worker.echo")


class EchoWorker(BaseWorker):
    """Echo worker: returns the payload it receives."""

    async def process(self, event: TempoEvent) -> Optional[str]:
        """Echo back the payload."""
        input_data = event.payload.get("input", "")
        logger.info("EchoWorker received: %s", input_data)
        return f"echo: {input_data}"
