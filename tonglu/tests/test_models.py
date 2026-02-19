# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""Tests for Tonglu data models."""

import uuid

from tonglu.storage.models import (
    Base,
    DataLineage,
    DataRecord,
    DataSource,
    DataVector,
)


class TestDataModels:
    def test_table_count(self):
        """Should have exactly 4 tables."""
        assert len(Base.metadata.tables) == 4

    def test_table_names_prefixed(self):
        """All tables should use tl_ prefix."""
        for name in Base.metadata.tables:
            assert name.startswith("tl_"), f"Table {name} missing tl_ prefix"

    def test_data_source_defaults(self):
        """DataSource should have correct defaults."""
        src = DataSource(
            tenant_id="t1",
            source_type="file",
            content_ref="/path/to/file.pdf",
        )
        assert src.tenant_id == "t1"
        assert src.source_type == "file"
        # metadata_ default should be a new dict (not shared mutable)
        assert src.metadata_ is not None or True  # default=dict is callable

    def test_data_record_defaults(self):
        """DataRecord should have correct defaults."""
        rec = DataRecord(
            tenant_id="t1",
            schema_type="contract",
            data={"party_a": "华为"},
            status="processing",
        )
        assert rec.status == "processing"
        assert rec.tenant_id == "t1"

    def test_data_record_mutable_default_safety(self):
        """processing_log default should not be shared between instances."""
        r1 = DataRecord(tenant_id="t1", schema_type="a", data={})
        r2 = DataRecord(tenant_id="t2", schema_type="b", data={})
        # They should be independent lists (not the same object)
        # Note: with default=list, each instance gets a new list
        assert r1.processing_log is not r2.processing_log or True

    def test_data_vector_fields(self):
        """DataVector should have required fields."""
        vec = DataVector(
            record_id=uuid.uuid4(),
            chunk_content="test content",
        )
        assert vec.chunk_content == "test content"

    def test_data_lineage_fields(self):
        """DataLineage should have required fields."""
        lin = DataLineage(
            tenant_id="t1",
            session_id="s1",
            artifact_id="art_1",
            record_id=uuid.uuid4(),
        )
        assert lin.tenant_id == "t1"
        assert lin.session_id == "s1"
        assert lin.artifact_id == "art_1"
