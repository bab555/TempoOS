# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Tonglu HTTP Client — TempoOS side interface to the Tonglu Data Service.

Used by data nodes (data_query, data_ingest, file_parser) to interact
with the Tonglu API over HTTP.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("tempo.tonglu_client")


class TongluClient:
    """
    铜炉 HTTP API 客户端。

    Usage:
        client = TongluClient("http://localhost:8100")
        results = await client.query("华为的合同", tenant_id="default")
    """

    def __init__(self, base_url: str = "http://localhost:8100") -> None:
        self._base_url = base_url
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    # ── Query ─────────────────────────────────────────────────

    async def query(
        self,
        intent: str,
        filters: Optional[Dict[str, Any]] = None,
        tenant_id: str = "default",
        mode: str = "hybrid",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Semantic + structured query."""
        resp = await self._client.post(
            "/api/query",
            json={
                "query": intent,
                "mode": mode,
                "filters": filters or {},
                "tenant_id": tenant_id,
                "limit": limit,
            },
        )
        resp.raise_for_status()
        return resp.json()["results"]

    # ── Ingest ────────────────────────────────────────────────

    async def ingest(
        self,
        data: Any,
        tenant_id: str,
        schema_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Ingest text/JSON data → returns record_id."""
        resp = await self._client.post(
            "/api/ingest/text",
            json={
                "data": data,
                "tenant_id": tenant_id,
                "schema_type": schema_type,
                "metadata": metadata,
            },
        )
        resp.raise_for_status()
        return resp.json()["record_id"]

    async def upload(
        self,
        file_path: str,
        file_name: str,
        tenant_id: str,
        schema_type: Optional[str] = None,
    ) -> str:
        """Upload a file → returns task_id for polling."""
        with open(file_path, "rb") as f:
            resp = await self._client.post(
                "/api/ingest/file",
                files={"file": (file_name, f)},
                data={
                    "tenant_id": tenant_id,
                    "schema_type": schema_type or "",
                },
            )
        resp.raise_for_status()
        return resp.json()["task_id"]

    # ── Record Access ─────────────────────────────────────────

    async def get_record(self, record_id: str) -> Dict[str, Any]:
        """Get a single record by ID."""
        resp = await self._client.get(f"/api/records/{record_id}")
        resp.raise_for_status()
        return resp.json()

    async def get_task(self, task_id: str) -> Dict[str, Any]:
        """Query async task processing status."""
        resp = await self._client.get(f"/api/tasks/{task_id}")
        resp.raise_for_status()
        return resp.json()

    # ── Lifecycle ─────────────────────────────────────────────

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def health_check(self) -> bool:
        """Check if Tonglu service is reachable."""
        try:
            resp = await self._client.get("/health")
            return resp.status_code == 200
        except Exception:
            return False
