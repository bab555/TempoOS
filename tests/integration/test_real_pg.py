# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Integration tests with REAL PostgreSQL."""

import uuid
import pytest
from tempo_os.storage.repositories import (
    SessionRepository, FlowRepository, EventRepository,
    IdempotencyRepository, NodeRegistryRepository,
)
from tempo_os.protocols.schema import TempoEvent
from tempo_os.protocols.events import CMD_EXECUTE, STEP_DONE


class TestRealPGSessions:
    @pytest.mark.asyncio
    async def test_create_and_get_session(self, real_db):
        async with real_db() as db:
            repo = SessionRepository(db)
            sid = await repo.create("test_tenant", flow_id="echo_flow", params={"x": 1})
            await db.commit()

            session = await repo.get(sid)
            assert session is not None
            assert session.tenant_id == "test_tenant"
            assert session.flow_id == "echo_flow"
            assert session.current_state == "idle"

    @pytest.mark.asyncio
    async def test_update_state(self, real_db):
        async with real_db() as db:
            repo = SessionRepository(db)
            sid = await repo.create("test_tenant")
            await db.commit()

            await repo.update_state(sid, "working", "running")
            await db.commit()

            session = await repo.get(sid)
            assert session.current_state == "working"
            assert session.session_state == "running"

    @pytest.mark.asyncio
    async def test_list_by_tenant(self, real_db):
        async with real_db() as db:
            repo = SessionRepository(db)
            await repo.create("tenant_list_test")
            await repo.create("tenant_list_test")
            await repo.create("other_tenant")
            await db.commit()

            sessions = await repo.list_by_tenant("tenant_list_test")
            assert len(sessions) == 2

    @pytest.mark.asyncio
    async def test_mark_completed(self, real_db):
        async with real_db() as db:
            repo = SessionRepository(db)
            sid = await repo.create("test_tenant")
            await db.commit()

            await repo.mark_completed(sid)
            await db.commit()

            session = await repo.get(sid)
            assert session.session_state == "completed"
            assert session.completed_at is not None


class TestRealPGFlows:
    @pytest.mark.asyncio
    async def test_create_and_get_flow(self, real_db):
        async with real_db() as db:
            repo = FlowRepository(db)
            await repo.create("test_flow", "Test Flow", "states: [a, b]")
            await db.commit()

            flow = await repo.get("test_flow")
            assert flow is not None
            assert flow.name == "Test Flow"
            assert flow.yaml_content == "states: [a, b]"

    @pytest.mark.asyncio
    async def test_upsert_flow(self, real_db):
        async with real_db() as db:
            repo = FlowRepository(db)
            await repo.create("upsert_flow", "V1", "states: [a]")
            await db.commit()

            await repo.create("upsert_flow", "V2", "states: [a, b]")
            await db.commit()

            flow = await repo.get("upsert_flow")
            assert flow.name == "V2"

    @pytest.mark.asyncio
    async def test_list_all(self, real_db):
        async with real_db() as db:
            repo = FlowRepository(db)
            await repo.create("flow_a", "A", "yaml_a")
            await repo.create("flow_b", "B", "yaml_b")
            await db.commit()

            flows = await repo.list_all()
            assert len(flows) >= 2


class TestRealPGEvents:
    @pytest.mark.asyncio
    async def test_append_and_replay(self, real_db):
        async with real_db() as db:
            # Need a session first
            sess_repo = SessionRepository(db)
            sid = await sess_repo.create("test_tenant")
            await db.commit()

            event_repo = EventRepository(db)

            evt1 = TempoEvent.create(
                type=CMD_EXECUTE, source="test",
                tenant_id="test_tenant", session_id=str(sid),
            )
            await event_repo.append(evt1, from_state="idle", to_state="working")

            evt2 = TempoEvent.create(
                type=STEP_DONE, source="node",
                tenant_id="test_tenant", session_id=str(sid),
            )
            await event_repo.append(evt2, from_state="working", to_state="done")
            await db.commit()

            # Replay
            events = await event_repo.replay(sid)
            assert len(events) == 2
            assert events[0].event_type == CMD_EXECUTE
            assert events[1].event_type == STEP_DONE
            assert events[0].from_state == "idle"
            assert events[1].to_state == "done"

    @pytest.mark.asyncio
    async def test_list_by_session(self, real_db):
        async with real_db() as db:
            sess_repo = SessionRepository(db)
            sid = await sess_repo.create("test_tenant")
            await db.commit()

            event_repo = EventRepository(db)
            for i in range(5):
                evt = TempoEvent.create(
                    type=STEP_DONE, source=f"node_{i}",
                    tenant_id="test_tenant", session_id=str(sid),
                )
                await event_repo.append(evt)
            await db.commit()

            events = await event_repo.list_by_session(sid, limit=3)
            assert len(events) == 3


class TestRealPGIdempotency:
    @pytest.mark.asyncio
    async def test_check_and_record(self, real_db):
        async with real_db() as db:
            repo = IdempotencyRepository(db)
            sid = uuid.uuid4()

            # Not yet recorded
            assert await repo.check(sid, "step_a", 1) is False

            # Record it
            await repo.record(sid, "step_a", 1, "success", "hash123")
            await db.commit()

            # Now it should exist
            assert await repo.check(sid, "step_a", 1) is True

            # Different attempt should not exist
            assert await repo.check(sid, "step_a", 2) is False

    @pytest.mark.asyncio
    async def test_max_attempt(self, real_db):
        async with real_db() as db:
            repo = IdempotencyRepository(db)
            sid = uuid.uuid4()

            await repo.record(sid, "step_x", 1, "error")
            await repo.record(sid, "step_x", 2, "error")
            await repo.record(sid, "step_x", 3, "success")
            await db.commit()

            max_a = await repo.get_max_attempt(sid, "step_x")
            assert max_a == 3


class TestRealPGNodeRegistry:
    @pytest.mark.asyncio
    async def test_register_and_list(self, real_db):
        async with real_db() as db:
            repo = NodeRegistryRepository(db)
            await repo.register("echo", "builtin", "Echo Node")
            await repo.register("ext_svc", "webhook", "External", endpoint="http://x.com/run")
            await db.commit()

            nodes = await repo.list_all()
            assert len(nodes) >= 2

            builtin_only = await repo.list_all(node_type="builtin")
            assert all(n.node_type == "builtin" for n in builtin_only)

    @pytest.mark.asyncio
    async def test_get_node(self, real_db):
        async with real_db() as db:
            repo = NodeRegistryRepository(db)
            await repo.register("test_node", "builtin", "Test")
            await db.commit()

            node = await repo.get("test_node")
            assert node is not None
            assert node.name == "Test"
