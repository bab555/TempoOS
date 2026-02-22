# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
ChatStore — Redis List-based conversation history storage.

Stores the complete chat history for each session as a Redis List.
Each entry is a JSON-serialized ChatMessage. The list is append-only
(RPUSH) and supports paginated reads (LRANGE).

This is the "source of truth" for conversation state. The frontend
can pull history via API; the ContextBuilder reads from here to
construct LLM context with trimming/summarization.

Redis key: tempo:{tenant_id}:chat:{session_id}
TTL: CHAT_HISTORY_TTL (default 24h, longer than session for review)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis

from tempo_os.kernel.namespace import get_chat_key

logger = logging.getLogger("tempo.chat_store")

DEFAULT_CHAT_TTL = 86400  # 24 hours


class ChatMessage:
    """Structured chat message for storage."""

    __slots__ = ("id", "role", "content", "ts", "type", "tool_name",
                 "tool_call_id", "files", "ui_schema", "extra")

    def __init__(
        self,
        role: str,
        content: str,
        *,
        msg_id: Optional[str] = None,
        ts: Optional[float] = None,
        msg_type: str = "text",
        tool_name: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        files: Optional[List[Dict[str, Any]]] = None,
        ui_schema: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ):
        self.id = msg_id or str(uuid.uuid4())
        self.role = role
        self.content = content
        self.ts = ts or time.time()
        self.type = msg_type
        self.tool_name = tool_name
        self.tool_call_id = tool_call_id
        self.files = files
        self.ui_schema = ui_schema
        self.extra = extra

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "ts": self.ts,
            "type": self.type,
        }
        if self.tool_name:
            d["tool_name"] = self.tool_name
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.files:
            d["files"] = self.files
        if self.ui_schema:
            d["ui_schema"] = self.ui_schema
        if self.extra:
            d["extra"] = self.extra
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> ChatMessage:
        return cls(
            role=d["role"],
            content=d.get("content", ""),
            msg_id=d.get("id"),
            ts=d.get("ts"),
            msg_type=d.get("type", "text"),
            tool_name=d.get("tool_name"),
            tool_call_id=d.get("tool_call_id"),
            files=d.get("files"),
            ui_schema=d.get("ui_schema"),
            extra=d.get("extra"),
        )

    @classmethod
    def from_json(cls, raw: str) -> ChatMessage:
        return cls.from_dict(json.loads(raw))

    def to_llm_message(self) -> Dict[str, Any]:
        """Convert to DashScope-compatible message format."""
        msg: Dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_name and self.role == "tool":
            msg["name"] = self.tool_name
        return msg


class ChatStore:
    """
    Tenant-scoped conversation history backed by Redis List.

    Thread-safe: all operations are atomic Redis commands.
    """

    def __init__(
        self,
        redis: aioredis.Redis,
        tenant_id: str,
        ttl: int = DEFAULT_CHAT_TTL,
    ) -> None:
        self._redis = redis
        self._tenant_id = tenant_id
        self._ttl = ttl

    def _key(self, session_id: str) -> str:
        return get_chat_key(self._tenant_id, session_id)

    async def append(self, session_id: str, msg: ChatMessage) -> int:
        """Append a single message. Returns new list length."""
        key = self._key(session_id)
        length = await self._redis.rpush(key, msg.to_json())
        await self._redis.expire(key, self._ttl)
        return length

    async def append_batch(self, session_id: str, msgs: List[ChatMessage]) -> int:
        """Append multiple messages atomically. Returns new list length."""
        if not msgs:
            return await self.count(session_id)
        key = self._key(session_id)
        serialized = [m.to_json() for m in msgs]
        length = await self._redis.rpush(key, *serialized)
        await self._redis.expire(key, self._ttl)
        return length

    async def get_history(
        self,
        session_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> List[ChatMessage]:
        """Read messages with pagination (oldest first)."""
        key = self._key(session_id)
        raw_list = await self._redis.lrange(key, offset, offset + limit - 1)
        return [ChatMessage.from_json(raw) for raw in raw_list]

    async def get_recent(self, session_id: str, n: int = 20) -> List[ChatMessage]:
        """Read the most recent N messages."""
        key = self._key(session_id)
        raw_list = await self._redis.lrange(key, -n, -1)
        return [ChatMessage.from_json(raw) for raw in raw_list]

    async def get_all(self, session_id: str) -> List[ChatMessage]:
        """Read all messages (use with caution on long conversations)."""
        key = self._key(session_id)
        raw_list = await self._redis.lrange(key, 0, -1)
        return [ChatMessage.from_json(raw) for raw in raw_list]

    async def count(self, session_id: str) -> int:
        """Return the total number of messages in this session."""
        key = self._key(session_id)
        return await self._redis.llen(key)

    async def clear(self, session_id: str) -> None:
        """Delete all chat history for a session."""
        key = self._key(session_id)
        await self._redis.delete(key)

    async def touch(self, session_id: str) -> None:
        """Refresh TTL without adding messages."""
        key = self._key(session_id)
        await self._redis.expire(key, self._ttl)
