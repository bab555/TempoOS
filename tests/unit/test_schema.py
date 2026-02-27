# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for TempoEvent schema."""

import pytest
from tempo_os.protocols.schema import TempoEvent
from tempo_os.protocols.events import CMD_EXECUTE, EVENT_RESULT


class TestTempoEvent:
    def test_create_valid_event(self):
        evt = TempoEvent.create(
            type=CMD_EXECUTE,
            source="test",
            tenant_id="t_001",
            session_id="s_001",
            payload={"input": "hello"},
        )
        assert evt.type == CMD_EXECUTE
        assert evt.source == "test"
        assert evt.tenant_id == "t_001"
        assert evt.session_id == "s_001"
        assert evt.payload == {"input": "hello"}
        assert evt.target == "*"
        assert evt.priority == 5

    def test_type_must_be_uppercase(self):
        with pytest.raises(ValueError, match="UPPERCASE"):
            TempoEvent.create(
                type="lowercase_bad",
                source="test",
                tenant_id="t_001",
                session_id="s_001",
            )

    def test_id_is_valid_uuid(self):
        evt = TempoEvent.create(
            type=CMD_EXECUTE,
            source="test",
            tenant_id="t_001",
            session_id="s_001",
        )
        import uuid
        uuid.UUID(evt.id)  # Should not raise

    def test_json_roundtrip(self):
        evt = TempoEvent.create(
            type=EVENT_RESULT,
            source="worker_echo",
            tenant_id="t_001",
            session_id="s_001",
            payload={"result": "ok"},
        )
        json_str = evt.to_json()
        restored = TempoEvent.from_json(json_str)
        assert restored.type == evt.type
        assert restored.source == evt.source
        assert restored.tenant_id == evt.tenant_id
        assert restored.payload == evt.payload

    def test_dict_roundtrip(self):
        evt = TempoEvent.create(
            type=CMD_EXECUTE,
            source="kernel",
            tenant_id="t_002",
            session_id="s_002",
        )
        d = evt.to_dict()
        restored = TempoEvent.from_dict(d)
        assert restored.id == evt.id
        assert restored.type == evt.type

    def test_tenant_id_required(self):
        with pytest.raises(Exception):
            TempoEvent(
                type=CMD_EXECUTE,
                source="test",
                session_id="s_001",
                # missing tenant_id
            )

    def test_session_id_required(self):
        with pytest.raises(Exception):
            TempoEvent(
                type=CMD_EXECUTE,
                source="test",
                tenant_id="t_001",
                # missing session_id
            )

    def test_priority_bounds(self):
        with pytest.raises(Exception):
            TempoEvent.create(
                type=CMD_EXECUTE, source="test",
                tenant_id="t_001", session_id="s_001",
                priority=11,
            )

    def test_trace_id_optional(self):
        evt = TempoEvent.create(
            type=CMD_EXECUTE, source="test",
            tenant_id="t_001", session_id="s_001",
            trace_id="trace-abc-123",
        )
        assert evt.trace_id == "trace-abc-123"
