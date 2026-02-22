# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Session Evictor — Redis → PG cold storage swap for idle sessions.

Runs as a background asyncio task inside Tonglu (alongside EventSinkListener).
Periodically scans TempoOS Redis keys and archives sessions whose TTL is
about to expire to PostgreSQL. When a request arrives for an archived session,
the Restorer (called from TempoOS agent.py) reads the snapshot back from PG
and restores it to Redis.

Architecture:
  Tonglu (this file):
    - SessionEvictor.start()  → periodic scan loop
    - SessionEvictor.archive_session()  → dump Redis → PG
    - SessionEvictor.restore_session()  → PG → Redis (called via Tonglu API)

  TempoOS (agent.py):
    - On request entry, if ChatStore is empty for session_id,
      call Tonglu's restore endpoint before proceeding.

Redis key patterns scanned:
  - tempo:{tenant_id}:session:{session_id}   (Blackboard Hash)
  - tempo:{tenant_id}:chat:{session_id}      (ChatStore List)
  - tempo:{tenant_id}:session:{session_id}:results:{tool}  (accumulated results)
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import redis.asyncio as aioredis

from tonglu.storage.models import SessionSnapshot
from tonglu.storage.repositories import DataRepository

logger = logging.getLogger("tonglu.session_evictor")

TOOL_NAMES = ("search", "data_query")


class SessionEvictor:
    """
    Periodic background task that archives idle Redis sessions to PG.

    Scan strategy (方案 B — 定时扫描):
      Every `scan_interval` seconds, iterate all session keys for configured
      tenants. If a session key's remaining TTL is below `ttl_threshold`,
      dump its full state to PG before Redis naturally expires it.
    """

    def __init__(
        self,
        redis_url: str,
        repo: DataRepository,
        tenant_ids: List[str],
        scan_interval: int = 300,
        ttl_threshold: int = 300,
    ) -> None:
        self._redis_url = redis_url
        self._repo = repo
        self._tenant_ids = tenant_ids
        self._scan_interval = scan_interval
        self._ttl_threshold = ttl_threshold
        self._running = False
        self._redis: Optional[aioredis.Redis] = None
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the evictor as a background asyncio task."""
        self._running = True
        self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        self._task = asyncio.create_task(self._scan_loop())
        logger.info(
            "Session Evictor started — interval=%ds threshold=%ds tenants=%s",
            self._scan_interval, self._ttl_threshold,
            ",".join(self._tenant_ids),
        )

    async def _scan_loop(self) -> None:
        """Main periodic scan loop."""
        while self._running:
            try:
                await self._scan_all_tenants()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Session Evictor scan error: %s", e, exc_info=True)

            try:
                await asyncio.sleep(self._scan_interval)
            except asyncio.CancelledError:
                break

    async def _scan_all_tenants(self) -> None:
        """Scan all configured tenants for expiring sessions."""
        total_archived = 0
        for tenant_id in self._tenant_ids:
            count = await self._scan_tenant(tenant_id)
            total_archived += count
        if total_archived > 0:
            logger.info("Session Evictor archived %d sessions this cycle", total_archived)

    async def _scan_tenant(self, tenant_id: str) -> int:
        """Scan a single tenant's session keys and archive expiring ones."""
        pattern = f"tempo:{tenant_id}:session:*"
        archived = 0
        seen_sessions: Set[str] = set()

        async for key in self._redis.scan_iter(match=pattern, count=100):
            session_id = self._extract_session_id(key, tenant_id)
            if not session_id or session_id in seen_sessions:
                continue
            # Skip sub-keys like session:{sid}:artifacts or session:{sid}:results:*
            if ":" in session_id:
                continue
            seen_sessions.add(session_id)

            ttl = await self._redis.ttl(key)
            # ttl == -1 means no expiry (shouldn't happen after our fix, but handle it)
            # ttl == -2 means key already expired
            if ttl == -2:
                continue

            if 0 < ttl <= self._ttl_threshold:
                try:
                    await self.archive_session(tenant_id, session_id)
                    archived += 1
                except Exception as e:
                    logger.error(
                        "Failed to archive session %s/%s: %s",
                        tenant_id, session_id, e, exc_info=True,
                    )

        return archived

    @staticmethod
    def _extract_session_id(key: str, tenant_id: str) -> Optional[str]:
        """Extract session_id from a Redis key like tempo:{tid}:session:{sid}."""
        prefix = f"tempo:{tenant_id}:session:"
        if not key.startswith(prefix):
            return None
        return key[len(prefix):]

    # ── Archive (Redis → PG) ─────────────────────────────────

    async def archive_session(self, tenant_id: str, session_id: str) -> bool:
        """
        Dump a complete session from Redis to PG.

        Reads:
          - Blackboard Hash (session state)
          - ChatStore List (conversation history)
          - Accumulated tool results Lists
        Writes:
          - SessionSnapshot row in PG (upsert)

        Returns True if archived successfully.
        """
        logger.info("Archiving session: tenant=%s session=%s", tenant_id, session_id)

        bb_key = f"tempo:{tenant_id}:session:{session_id}"
        chat_key = f"tempo:{tenant_id}:chat:{session_id}"

        # Read Blackboard state
        bb_data = await self._redis.hgetall(bb_key)
        if not bb_data:
            bb_data = {}

        # Deserialize Blackboard values (they may be JSON strings)
        blackboard: Dict[str, Any] = {}
        for k, v in bb_data.items():
            try:
                blackboard[k] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                blackboard[k] = v

        # Read ChatStore history
        chat_raw = await self._redis.lrange(chat_key, 0, -1)
        chat_history = []
        for raw in chat_raw:
            try:
                chat_history.append(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                chat_history.append({"raw": raw})

        # Read accumulated tool results
        tool_results: Dict[str, list] = {}
        for tool in TOOL_NAMES:
            results_key = f"tempo:{tenant_id}:session:{session_id}:results:{tool}"
            raw_list = await self._redis.lrange(results_key, 0, -1)
            if raw_list:
                parsed = []
                for raw in raw_list:
                    try:
                        parsed.append(json.loads(raw))
                    except (json.JSONDecodeError, TypeError):
                        parsed.append(raw)
                tool_results[tool] = parsed

        # Skip if there's nothing worth archiving
        if not chat_history and not blackboard:
            logger.debug("Session %s/%s is empty, skipping archive", tenant_id, session_id)
            return False

        # Extract cached summary and routed scene from blackboard
        chat_summary = blackboard.pop("_chat_summary", None)
        chat_summary_count = blackboard.pop("_chat_summary_count", None)
        routed_scene = blackboard.pop("_routed_scene", None)

        # If summary_count was stored, keep it in the summary field for restore
        if chat_summary and chat_summary_count:
            chat_summary = json.dumps({
                "text": chat_summary,
                "count": chat_summary_count,
            }, ensure_ascii=False)

        snapshot = SessionSnapshot(
            session_id=session_id,
            tenant_id=tenant_id,
            chat_history=chat_history,
            blackboard=blackboard,
            tool_results=tool_results,
            chat_summary=chat_summary,
            routed_scene=routed_scene,
            archived_at=datetime.now(timezone.utc),
        )

        await self._repo.save_snapshot(snapshot)
        logger.info(
            "Session archived: tenant=%s session=%s msgs=%d bb_keys=%d",
            tenant_id, session_id, len(chat_history), len(blackboard),
        )
        return True

    # ── Restore (PG → Redis) ─────────────────────────────────

    async def restore_session(
        self,
        tenant_id: str,
        session_id: str,
        session_ttl: int = 1800,
        chat_ttl: int = 86400,
    ) -> bool:
        """
        Restore a session from PG snapshot back to Redis.

        Called by TempoOS agent.py (via Tonglu API) when a request arrives
        for a session that no longer exists in Redis.

        Returns True if a snapshot was found and restored.
        """
        snapshot = await self._repo.get_snapshot(session_id)
        if not snapshot:
            return False

        if snapshot.tenant_id != tenant_id:
            logger.warning(
                "Snapshot tenant mismatch: expected=%s got=%s session=%s",
                tenant_id, snapshot.tenant_id, session_id,
            )
            return False

        logger.info("Restoring session: tenant=%s session=%s", tenant_id, session_id)

        bb_key = f"tempo:{tenant_id}:session:{session_id}"
        chat_key = f"tempo:{tenant_id}:chat:{session_id}"

        # Restore Blackboard state (always JSON-serialize to match TenantBlackboard.set_state)
        if snapshot.blackboard:
            mapping = {}
            for k, v in snapshot.blackboard.items():
                mapping[k] = json.dumps(v, ensure_ascii=False)
            if mapping:
                await self._redis.hset(bb_key, mapping=mapping)
                await self._redis.expire(bb_key, session_ttl)

        # Restore cached summary and routed scene to Blackboard
        if snapshot.chat_summary:
            try:
                summary_data = json.loads(snapshot.chat_summary)
                if isinstance(summary_data, dict) and "text" in summary_data:
                    await self._redis.hset(bb_key, "_chat_summary", summary_data["text"])
                    await self._redis.hset(bb_key, "_chat_summary_count", str(summary_data.get("count", 0)))
                else:
                    await self._redis.hset(bb_key, "_chat_summary", snapshot.chat_summary)
            except (json.JSONDecodeError, TypeError):
                await self._redis.hset(bb_key, "_chat_summary", snapshot.chat_summary)
            await self._redis.expire(bb_key, session_ttl)

        if snapshot.routed_scene:
            await self._redis.hset(bb_key, "_routed_scene", snapshot.routed_scene)
            await self._redis.expire(bb_key, session_ttl)

        # Restore ChatStore history
        if snapshot.chat_history:
            serialized = [
                json.dumps(msg, ensure_ascii=False) for msg in snapshot.chat_history
            ]
            await self._redis.rpush(chat_key, *serialized)
            await self._redis.expire(chat_key, chat_ttl)

        # Restore accumulated tool results
        if snapshot.tool_results:
            for tool, results in snapshot.tool_results.items():
                if results:
                    results_key = f"tempo:{tenant_id}:session:{session_id}:results:{tool}"
                    serialized = [json.dumps(r, ensure_ascii=False) for r in results]
                    await self._redis.rpush(results_key, *serialized)
                    await self._redis.expire(results_key, session_ttl)

        # Mark as restored in PG
        await self._repo.mark_snapshot_restored(session_id)

        logger.info(
            "Session restored: tenant=%s session=%s msgs=%d",
            tenant_id, session_id, len(snapshot.chat_history or []),
        )
        return True

    async def stop(self) -> None:
        """Gracefully stop the evictor."""
        logger.info("Session Evictor stopping...")
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._redis:
            await self._redis.close()

        logger.info("Session Evictor stopped.")
