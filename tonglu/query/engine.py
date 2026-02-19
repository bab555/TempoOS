# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Query Engine — Unified SQL + Vector + Hybrid search.

Supports three modes:
- sql:    JSONB field-level exact matching
- vector: Semantic similarity search via pgvector
- hybrid: Merge SQL + Vector results with deduplication
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from tonglu.services.llm_service import LLMService
from tonglu.storage.repositories import DataRepository

logger = logging.getLogger("tonglu.query")


class QueryEngine:
    """
    统一查询引擎 — SQL + 向量 + 混合。

    Usage:
        engine = QueryEngine(repo, llm_service)
        results = await engine.query("华为的合同", mode="hybrid", tenant_id="default")
    """

    def __init__(self, repo: DataRepository, llm_service: LLMService) -> None:
        self._repo = repo
        self._llm = llm_service

    async def query(
        self,
        intent: str,
        mode: str = "hybrid",
        filters: Optional[Dict[str, Any]] = None,
        tenant_id: str = "default",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Execute a query in the specified mode.

        Args:
            intent: Natural language query or keyword.
            mode: "sql" / "vector" / "hybrid"
            filters: Pre-structured filters (bypass LLM intent parsing).
            tenant_id: Tenant scope.
            limit: Max results.

        Returns:
            List of matching records as dicts.
        """
        if mode == "sql":
            return await self._sql_query(intent, filters, tenant_id, limit)
        elif mode == "vector":
            return await self._vector_query(intent, tenant_id, limit)
        else:  # hybrid (default)
            sql_results, vec_results = await self._parallel_query(
                intent, filters, tenant_id, limit,
            )
            return self._merge_and_rank(sql_results, vec_results, limit)

    # ── SQL Query ─────────────────────────────────────────────

    async def _sql_query(
        self,
        intent: str,
        filters: Optional[Dict[str, Any]],
        tenant_id: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        """JSONB field-level exact matching."""
        if filters:
            records = await self._repo.list_records(
                tenant_id=tenant_id,
                schema_type=filters.get("schema_type"),
                offset=0,
                limit=limit,
                data_filters=filters.get("data_conditions"),
            )
        else:
            # Use LLM to convert natural language to structured filters
            conditions = await self._intent_to_filters(intent)
            records = await self._repo.list_records(
                tenant_id=tenant_id,
                schema_type=conditions.get("schema_type"),
                offset=conditions.get("offset", 0),
                limit=limit,
                data_filters=conditions.get("data_conditions"),
            )

        return [self._record_to_dict(r) for r in records]

    # ── Vector Query ──────────────────────────────────────────

    async def _vector_query(
        self,
        intent: str,
        tenant_id: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Semantic similarity search via pgvector."""
        query_embedding = (await self._llm.embed([intent]))[0]
        return await self._repo.vector_search(
            embedding=query_embedding,
            tenant_id=tenant_id,
            limit=limit,
        )

    # ── Hybrid Query ──────────────────────────────────────────

    async def _parallel_query(
        self,
        intent: str,
        filters: Optional[Dict[str, Any]],
        tenant_id: str,
        limit: int,
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Run SQL and Vector queries in parallel."""
        sql_task = self._sql_query(intent, filters, tenant_id, limit)
        vec_task = self._vector_query(intent, tenant_id, limit)
        sql_results, vec_results = await asyncio.gather(sql_task, vec_task)
        return sql_results, vec_results

    def _merge_and_rank(
        self,
        sql_results: List[Dict[str, Any]],
        vec_results: List[Dict[str, Any]],
        limit: int,
    ) -> List[Dict[str, Any]]:
        """
        Merge SQL and Vector results with deduplication.

        SQL results are prioritized (exact match has higher weight).
        """
        seen: set[str] = set()
        merged: List[Dict[str, Any]] = []

        # SQL results first (exact match priority)
        for r in sql_results:
            rid = str(r.get("id", ""))
            if rid and rid not in seen:
                seen.add(rid)
                r["_match_type"] = "sql"
                merged.append(r)

        # Vector results supplement
        for r in vec_results:
            rid = str(r.get("id", ""))
            if rid and rid not in seen:
                seen.add(rid)
                r["_match_type"] = "vector"
                merged.append(r)

        return merged[:limit]

    # ── Intent Parsing ────────────────────────────────────────

    async def _intent_to_filters(self, intent: str) -> Dict[str, Any]:
        """LLM: Convert natural language query to structured filters."""
        messages = [
            {
                "role": "system",
                "content": (
                    "将用户的查询意图转为 JSON 过滤条件。"
                    "可用字段：schema_type (数据类型), data_conditions (JSONB 路径条件)。"
                    '返回 JSON 格式，例如：{"schema_type": "contract", "data_conditions": {"party_a": "华为"}}'
                ),
            },
            {
                "role": "user",
                "content": intent,
            },
        ]
        result = await self._llm.call(task_type="route", messages=messages)

        # Parse JSON response
        cleaned = result.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning(
                "LLM intent parsing failed, returning empty filters. Raw: %s",
                result[:200],
            )
            return {}

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _record_to_dict(record: Any) -> Dict[str, Any]:
        """Convert a DataRecord ORM object to a plain dict."""
        return {
            "id": str(record.id),
            "tenant_id": record.tenant_id,
            "schema_type": record.schema_type,
            "data": record.data,
            "summary": record.summary,
            "status": record.status,
            "created_at": record.created_at.isoformat() if record.created_at else None,
        }
