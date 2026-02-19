# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Event Sink — Subscribe to TempoOS Redis Bus, auto-persist Blackboard artifacts.

Alignment with TempoOS:
- Bus channel: tempo:{tenant_id}:events (tenant-scoped Pub/Sub)
- Message body: TempoEvent JSON (see tempo_os/protocols/schema.py)
- Artifact storage:
  - Content:  tempo:{tenant_id}:artifact:{artifact_id} (String JSON)
  - Session:  tempo:{tenant_id}:session:{session_id}:artifacts (Set of artifact_ids)
- Deduplication: tl_data_lineage table with unique constraint on (tenant, session, artifact)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Set

import redis.asyncio as aioredis

from tonglu.pipeline.ingestion import IngestionPipeline
from tonglu.storage.repositories import DataRepository

logger = logging.getLogger("tonglu.event_sink")

# Events that indicate session activity worth checking for artifacts
TRIGGER_EVENTS = {"EVENT_RESULT", "EVENT_ERROR", "STATE_TRANSITION", "STEP_DONE"}

# File processing event: Agent Controller uploads file to OSS, Tonglu processes it
FILE_UPLOADED = "FILE_UPLOADED"
FILE_READY = "FILE_READY"


class EventSinkListener:
    """
    订阅 TempoOS Redis Bus，自动持久化 Blackboard 产物。

    工作流程：
    1. 监听 tenant-scoped Redis channel
    2. 收到触发事件后，读取该 session 的 artifact 列表
    3. 按 persist_rules 过滤
    4. 去重（通过 tl_data_lineage 唯一约束）
    5. 将匹配的 artifact 送入 IngestionPipeline
    """

    def __init__(
        self,
        redis_url: str,
        pipeline: IngestionPipeline,
        repo: DataRepository,
        persist_rules: List[str],
        tenant_ids: List[str],
    ) -> None:
        self._redis_url = redis_url
        self._pipeline = pipeline
        self._repo = repo
        self._persist_rules: Set[str] = set(persist_rules)
        self._tenant_ids = tenant_ids
        self._running = False
        self._redis: Optional[aioredis.Redis] = None
        self._pubsub: Optional[aioredis.client.PubSub] = None
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the event listener as a background asyncio task."""
        self._running = True
        self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        self._pubsub = self._redis.pubsub()

        # Subscribe to tenant-scoped channels
        channels = [f"tempo:{tid}:events" for tid in self._tenant_ids]
        if channels:
            await self._pubsub.subscribe(*channels)
            logger.info(
                "Event Sink started — listening on channels: %s",
                ", ".join(channels),
            )
        else:
            logger.warning("Event Sink started but no tenant channels configured")

        # Run the listener loop
        self._task = asyncio.create_task(self._listen_loop())

    async def _listen_loop(self) -> None:
        """Main event listening loop."""
        while self._running:
            try:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if message and message["type"] == "message":
                    try:
                        event = json.loads(message["data"])
                        await self._handle_event(event)
                    except json.JSONDecodeError:
                        logger.warning("Received non-JSON message on bus: %s", message["data"][:100])
                    except Exception as e:
                        logger.error("Error handling event: %s", e, exc_info=True)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Event Sink listener error: %s", e, exc_info=True)
                await asyncio.sleep(1)  # Brief pause before retry

    async def _handle_event(self, event: Dict[str, Any]) -> None:
        """Process a single TempoEvent (dict form)."""
        tenant_id = event.get("tenant_id")
        session_id = event.get("session_id")
        event_type = event.get("type")

        if not tenant_id or not session_id:
            return

        # Handle FILE_UPLOADED: pull file from OSS, parse, publish FILE_READY
        if event_type == FILE_UPLOADED:
            await self._handle_file_uploaded(event)
            return

        # Only process trigger events for artifact persistence
        if event_type not in TRIGGER_EVENTS:
            return

        logger.debug(
            "Event Sink trigger: type=%s tenant=%s session=%s",
            event_type, tenant_id, session_id,
        )

        # Read the session's current artifact list from TempoOS Blackboard (Redis Set)
        artifacts_set_key = f"tempo:{tenant_id}:session:{session_id}:artifacts"
        artifact_ids = await self._redis.smembers(artifacts_set_key)

        if not artifact_ids:
            return

        for artifact_id in artifact_ids:
            # Check persist rules
            if not self._match_rules(artifact_id):
                continue

            # Deduplication: check if already persisted
            if await self._repo.is_lineage_persisted(tenant_id, session_id, artifact_id):
                logger.debug(
                    "Artifact already persisted: tenant=%s session=%s artifact=%s",
                    tenant_id, session_id, artifact_id,
                )
                continue

            # Read artifact content from TempoOS Blackboard (String JSON)
            artifact_key = f"tempo:{tenant_id}:artifact:{artifact_id}"
            raw = await self._redis.get(artifact_key)
            if not raw:
                logger.warning(
                    "Artifact not found in Blackboard: %s", artifact_key,
                )
                continue

            # Ingest the artifact through the pipeline
            logger.info(
                "Event Sink ingesting: tenant=%s session=%s artifact=%s",
                tenant_id, session_id, artifact_id,
            )

            result = await self._pipeline.process(
                source_type="event",
                content_ref=raw,
                tenant_id=tenant_id,
                metadata={
                    "session_id": session_id,
                    "artifact_id": artifact_id,
                    "source": "event_sink",
                    "trigger_event_type": event_type,
                    "trigger_event_id": event.get("id"),
                },
            )

            # Save lineage for deduplication
            if result.record_id:
                await self._repo.save_lineage(
                    tenant_id=tenant_id,
                    session_id=session_id,
                    artifact_id=artifact_id,
                    record_id=result.record_id,
                )
                logger.info(
                    "Event Sink persisted: artifact=%s → record=%s",
                    artifact_id, result.record_id,
                )
            else:
                logger.warning(
                    "Event Sink ingestion failed for artifact=%s: %s",
                    artifact_id, result.error,
                )

    def _match_rules(self, artifact_id: str) -> bool:
        """Check if an artifact_id matches any persist rule."""
        # Phase 1: exact match or prefix match
        for rule in self._persist_rules:
            if artifact_id == rule or artifact_id.startswith(rule + "_"):
                return True
        return False

    # ── FILE_UPLOADED Handler ────────────────────────────────

    async def _handle_file_uploaded(self, event: Dict[str, Any]) -> None:
        """
        Handle FILE_UPLOADED event: pull file from OSS, parse, publish FILE_READY.

        Payload expected:
          - file_id: unique ID for this file upload
          - file_url: OSS URL of the uploaded file
          - file_name: original filename (for parser selection)
          - file_type: MIME type (optional)
          - user_id: (optional)

        On success, publishes FILE_READY with:
          - file_id, file_url, file_name
          - text_content: parsed text from the file
          - record_id: Tonglu DataRecord ID (for future reference)
        """
        tenant_id = event.get("tenant_id", "")
        session_id = event.get("session_id", "")
        payload = event.get("payload", {})

        file_id = payload.get("file_id", "")
        file_url = payload.get("file_url", "")
        file_name = payload.get("file_name", "")

        if not file_url:
            logger.warning("FILE_UPLOADED event missing file_url, skipping")
            return

        logger.info(
            "FILE_UPLOADED received: tenant=%s session=%s file=%s url=%s",
            tenant_id, session_id, file_name, file_url,
        )

        text_content = ""
        record_id = None
        error_msg = ""

        try:
            # Use IngestionPipeline to process the file from URL
            result = await self._pipeline.process(
                source_type="url",
                content_ref=file_url,
                file_name=file_name,
                tenant_id=tenant_id,
                metadata={
                    "session_id": session_id,
                    "file_id": file_id,
                    "source": "file_upload",
                    "user_id": payload.get("user_id", ""),
                },
            )

            if result.status == "ready" and result.record_id:
                record_id = str(result.record_id)
                # Retrieve the parsed text from the DataRecord
                record = await self._repo.get_record(result.record_id)
                if record:
                    # Use summary + fields as text content for LLM
                    parts = []
                    if record.summary:
                        parts.append(record.summary)
                    if record.data:
                        import json as _json
                        parts.append(_json.dumps(record.data, ensure_ascii=False, indent=2))
                    text_content = "\n".join(parts) if parts else "(文件已解析但无文本内容)"
                else:
                    text_content = "(文件已处理但记录读取失败)"
            else:
                error_msg = result.error or "unknown error"
                text_content = f"(文件处理失败: {error_msg})"

        except Exception as e:
            logger.error(
                "FILE_UPLOADED processing failed: file=%s error=%s",
                file_name, e, exc_info=True,
            )
            text_content = f"(文件处理异常: {str(e)})"
            error_msg = str(e)

        # Publish FILE_READY back to EventBus
        ready_payload = {
            "file_id": file_id,
            "file_url": file_url,
            "file_name": file_name,
            "text_content": text_content,
        }
        if record_id:
            ready_payload["record_id"] = record_id
        if error_msg:
            ready_payload["error"] = error_msg

        ready_event_data = {
            "id": str(__import__("uuid").uuid4()),
            "type": FILE_READY,
            "source": "tonglu_event_sink",
            "target": "*",
            "tick": 0,
            "payload": ready_payload,
            "tenant_id": tenant_id,
            "session_id": session_id,
            "priority": 7,
        }

        channel = f"tempo:{tenant_id}:events"
        import json as _json
        await self._redis.publish(channel, _json.dumps(ready_event_data, ensure_ascii=False))

        logger.info(
            "FILE_READY published: tenant=%s session=%s file=%s text_len=%d",
            tenant_id, session_id, file_name, len(text_content),
        )

    async def stop(self) -> None:
        """Gracefully stop the listener."""
        logger.info("Event Sink stopping...")
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._pubsub:
            await self._pubsub.unsubscribe()
            await self._pubsub.close()

        if self._redis:
            await self._redis.close()

        logger.info("Event Sink stopped.")
