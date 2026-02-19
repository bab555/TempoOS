# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Tonglu Data Models — Three-Layer Storage.

Layer 1: data_sources   — 原始数据不可变存档
Layer 2: data_records   — LLM 清洗后的结构化资产（核心层）
Layer 3: data_vectors   — 语义向量索引

All tables use `tl_` prefix to coexist with TempoOS tables in the same PG instance.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship

try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    # Allow import without pgvector installed (for tests with mocks)
    Vector = None


class Base(DeclarativeBase):
    """Shared declarative base for all Tonglu models."""
    pass


# ── Layer 1: 源数据层 ─────────────────────────────────────────


class DataSource(Base):
    """
    原始数据的不可变存档。

    一旦写入不可修改，用于追溯和重新处理。
    """

    __tablename__ = "tl_data_sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    source_type = Column(String(20), nullable=False)  # file / text / url / event
    file_name = Column(String(512), nullable=True)
    content_ref = Column(Text, nullable=False)  # 文件路径或文本内容
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    records = relationship("DataRecord", back_populates="source", lazy="selectin")


# ── Layer 2: 标准资产层 ───────────────────────────────────────


class DataRecord(Base):
    """
    经过 LLM 清洗、标准化的业务资产。

    铜炉的核心层——所有查询和检索都基于此表。
    """

    __tablename__ = "tl_data_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    source_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tl_data_sources.id", ondelete="SET NULL"),
        nullable=True,
    )
    schema_type = Column(String(64), nullable=False, index=True)
    data = Column(JSONB, nullable=False)
    summary = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="processing")
    processing_log = Column(JSONB, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    source = relationship("DataSource", back_populates="records")
    vectors = relationship("DataVector", back_populates="record", lazy="selectin")

    # Composite index: tenant + schema_type
    __table_args__ = (
        Index("idx_tl_records_tenant_type", "tenant_id", "schema_type"),
        Index("idx_tl_records_data", "data", postgresql_using="gin"),
    )


# ── Layer 3: 检索索引层 ───────────────────────────────────────


class DataVector(Base):
    """
    语义向量索引，为混合检索服务。

    每条 DataRecord 可对应多个向量切片（Phase 1 通常只有 1 个：summary 的向量）。
    """

    __tablename__ = "tl_data_vectors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    record_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tl_data_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_content = Column(Text, nullable=False)
    embedding = Column(Vector(1024)) if Vector else Column(Text)  # fallback for tests
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    record = relationship("DataRecord", back_populates="vectors")


# ── 数据血缘层（Event Sink 去重）──────────────────────────────


class DataLineage(Base):
    """
    记录 TempoOS 工作流产物与铜炉记录的映射关系。

    用途：
    1. Event Sink 去重：同一个 (tenant, session, artifact) 只入库一次
    2. 数据追溯：未来可查"这条数据是哪个流程、哪个步骤产生的"
    """

    __tablename__ = "tl_data_lineage"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False)
    session_id = Column(String(128), nullable=False)
    artifact_id = Column(String(256), nullable=False)
    record_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tl_data_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "session_id", "artifact_id",
            name="uq_tl_lineage_tenant_session_artifact",
        ),
    )
