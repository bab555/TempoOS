# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Data Repository — CRUD and query operations for Tonglu storage.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select, update, and_, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tonglu.storage.models import DataLineage, DataRecord, DataSource, DataVector

logger = logging.getLogger("tonglu.repository")


class DataRepository:
    """
    铜炉数据访问层。

    All methods create their own session and commit within it.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # ── Source (Layer 1) ──────────────────────────────────────

    async def save_source(self, source: DataSource) -> DataSource:
        """Persist a new DataSource and return it with generated ID."""
        async with self._session_factory() as session:
            session.add(source)
            await session.commit()
            await session.refresh(source)
            return source

    # ── Record (Layer 2) ──────────────────────────────────────

    async def save_record(self, record: DataRecord) -> DataRecord:
        """Persist a new DataRecord and return it with generated ID."""
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def get_record(self, record_id: UUID) -> Optional[DataRecord]:
        """Get a single record by ID."""
        async with self._session_factory() as session:
            return await session.get(DataRecord, record_id)

    async def list_records(
        self,
        tenant_id: str,
        schema_type: Optional[str] = None,
        offset: int = 0,
        limit: int = 20,
        data_filters: Optional[Dict[str, Any]] = None,
    ) -> List[DataRecord]:
        """
        List records with optional filtering.

        Args:
            tenant_id: Required tenant scope.
            schema_type: Optional filter by data type.
            offset: Pagination offset.
            limit: Pagination limit.
            data_filters: Optional JSONB field conditions,
                          e.g. {"amount__gt": 1000000}
        """
        async with self._session_factory() as session:
            stmt = (
                select(DataRecord)
                .where(DataRecord.tenant_id == tenant_id)
                .where(DataRecord.status == "ready")
                .order_by(DataRecord.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            if schema_type:
                stmt = stmt.where(DataRecord.schema_type == schema_type)

            # Phase 1: simple JSONB containment filter
            # e.g. data_filters = {"party_a": "华为"} → data @> '{"party_a": "华为"}'
            if data_filters:
                for key, value in data_filters.items():
                    stmt = stmt.where(
                        DataRecord.data[key].astext == str(value)
                    )

            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_record_status(
        self, record_id: UUID, status: str, log_entry: Optional[str] = None,
    ) -> None:
        """Update a record's status and optionally append to processing_log."""
        async with self._session_factory() as session:
            stmt = (
                update(DataRecord)
                .where(DataRecord.id == record_id)
                .values(status=status)
            )
            await session.execute(stmt)
            # Append log entry if provided
            if log_entry:
                record = await session.get(DataRecord, record_id)
                if record and record.processing_log is not None:
                    record.processing_log = [*record.processing_log, log_entry]
            await session.commit()

    # ── Vector (Layer 3) ──────────────────────────────────────

    async def save_vectors(self, vectors: List[DataVector]) -> None:
        """Persist a batch of DataVector entries."""
        async with self._session_factory() as session:
            session.add_all(vectors)
            await session.commit()

    async def vector_search(
        self,
        embedding: List[float],
        tenant_id: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Cosine similarity search on data_vectors, returning associated records.

        Uses pgvector's <=> operator for cosine distance.
        """
        async with self._session_factory() as session:
            embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

            stmt = text("""
                SELECT r.id, r.tenant_id, r.schema_type, r.data, r.summary,
                       r.status, r.created_at,
                       v.chunk_content,
                       (v.embedding <=> CAST(:embedding AS vector)) AS distance
                FROM tl_data_vectors v
                JOIN tl_data_records r ON r.id = v.record_id
                WHERE r.tenant_id = :tenant_id
                  AND r.status = 'ready'
                ORDER BY v.embedding <=> CAST(:embedding AS vector)
                LIMIT :limit
            """)

            result = await session.execute(
                stmt,
                {"embedding": embedding_str, "tenant_id": tenant_id, "limit": limit},
            )
            rows = result.mappings().all()
            return [dict(row) for row in rows]

    # ── Lineage (Event Sink 去重) ─────────────────────────────

    async def is_lineage_persisted(
        self, tenant_id: str, session_id: str, artifact_id: str,
    ) -> bool:
        """Check if an artifact from a session has already been persisted."""
        async with self._session_factory() as session:
            stmt = select(DataLineage.id).where(
                and_(
                    DataLineage.tenant_id == tenant_id,
                    DataLineage.session_id == session_id,
                    DataLineage.artifact_id == artifact_id,
                )
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None

    async def save_lineage(
        self,
        tenant_id: str,
        session_id: str,
        artifact_id: str,
        record_id: UUID,
    ) -> None:
        """Record a lineage entry (session artifact → tonglu record)."""
        async with self._session_factory() as session:
            lineage = DataLineage(
                tenant_id=tenant_id,
                session_id=session_id,
                artifact_id=artifact_id,
                record_id=record_id,
            )
            session.add(lineage)
            await session.commit()
