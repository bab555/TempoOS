# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Smoke test -- Full Tonglu with real PG + Redis + DashScope.

Run:  pytest tests/smoke/test_smoke_tonglu.py -v -s --timeout=300
"""

import json
import os
import tempfile
import uuid

import pytest

from tonglu.config import TongluSettings
from tonglu.services.llm_service import LLMService
from tonglu.parsers.registry import ParserRegistry
from tonglu.parsers.base import ParseResult
from tonglu.pipeline.ingestion import IngestionPipeline
from tonglu.query.engine import QueryEngine
from tonglu.storage.database import Database
from tonglu.storage.repositories import DataRepository

_settings = TongluSettings()

pytestmark = pytest.mark.skipif(
    not _settings.DASHSCOPE_API_KEY,
    reason="DASHSCOPE_API_KEY not configured",
)

TENANT = f"smoke_tl_{uuid.uuid4().hex[:6]}"


def _make_llm():
    return LLMService(
        api_key=_settings.DASHSCOPE_API_KEY,
        default_model=_settings.DASHSCOPE_DEFAULT_MODEL,
        embedding_model=_settings.DASHSCOPE_EMBEDDING_MODEL,
    )


async def _make_db():
    db = Database(_settings.DATABASE_URL)
    await db.init()
    return db


def _pg_available() -> bool:
    """Quick check if PG is reachable (sync, for skipif).

    Parses host/port from DATABASE_URL so it works with non-default ports
    like 15432.
    """
    import socket
    from urllib.parse import urlparse
    parsed = urlparse(_settings.DATABASE_URL.replace("+asyncpg", ""))
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5432
    try:
        s = socket.create_connection((host, port), timeout=2)
        s.close()
        return True
    except OSError:
        return False


_skip_no_pg = pytest.mark.skipif(
    not _pg_available(),
    reason=f"PostgreSQL not reachable (parsed from DATABASE_URL)",
)


# ── 1. Database ───────────────────────────────────────────────

@_skip_no_pg
class TestDatabase:
    @pytest.mark.asyncio
    async def test_tables_created(self):
        db = await _make_db()
        try:
            from sqlalchemy import text
            async with db.engine.connect() as conn:
                result = await conn.execute(text(
                    "SELECT tablename FROM pg_tables WHERE tablename LIKE 'tl_%'"
                ))
                tables = [row[0] for row in result.fetchall()]

            print(f"\n--- Tonglu tables: {tables} ---")
            assert "tl_data_sources" in tables
            assert "tl_data_records" in tables
            assert "tl_data_vectors" in tables
            assert "tl_data_lineage" in tables
            assert "tl_session_snapshots" in tables, (
                "tl_session_snapshots table missing — run scripts/init_db.sql"
            )
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_session_snapshot_crud(self):
        """Verify SessionSnapshot CRUD via DataRepository."""
        from tonglu.storage.models import SessionSnapshot
        from datetime import datetime, timezone

        db = await _make_db()
        try:
            repo = DataRepository(db.session_factory)
            snap_session = f"snap_smoke_{uuid.uuid4().hex[:6]}"

            # save
            snapshot = SessionSnapshot(
                session_id=snap_session,
                tenant_id=TENANT,
                chat_history=[{"role": "user", "content": "hello"}],
                blackboard={"key": "value"},
                tool_results={"search": [{"q": "test"}]},
                chat_summary="summary text",
                routed_scene="general",
                archived_at=datetime.now(timezone.utc),
            )
            await repo.save_snapshot(snapshot)

            # get
            snap = await repo.get_snapshot(snap_session)
            assert snap is not None
            assert snap.tenant_id == TENANT
            assert snap.chat_history == [{"role": "user", "content": "hello"}]
            assert snap.blackboard == {"key": "value"}
            assert snap.chat_summary == "summary text"
            assert snap.routed_scene == "general"
            print(f"\n--- SessionSnapshot saved & retrieved: {snap_session} ---")

            # mark restored
            await repo.mark_snapshot_restored(snap_session)
            snap2 = await repo.get_snapshot(snap_session)
            assert snap2.restored_at is not None
            print(f"  restored_at: {snap2.restored_at}")

            # delete
            await repo.delete_snapshot(snap_session)
            snap3 = await repo.get_snapshot(snap_session)
            assert snap3 is None
            print(f"  Deleted successfully")
        finally:
            await db.close()


# ── 2. LLM Service ───────────────────────────────────────────

class TestLLMService:
    @pytest.mark.asyncio
    async def test_call_route(self):
        llm = _make_llm()
        result = await llm.call(
            task_type="route",
            messages=[{"role": "user", "content": (
                "Determine the type: invoice, contract, quotation, other\n\n"
                "Party A: Shenzhen Tech. Party B: CSCEC. Amount: 50000 CNY."
            )}],
        )
        print(f"\n--- Type detection: '{result.strip()}' ---")
        assert len(result.strip()) > 0

    @pytest.mark.asyncio
    async def test_call_extract(self):
        llm = _make_llm()
        result = await llm.call(
            task_type="extract",
            messages=[
                {"role": "system", "content": (
                    "Extract fields and summary from contract. "
                    'Return JSON: {"fields": {...}, "summary": "..."}'
                )},
                {"role": "user", "content": (
                    "Contract HT-001. Party A: Shenzhen Tech. Party B: CSCEC. "
                    "Amount: 143390 CNY. Items: 10x ThinkPad, 10x Dell monitor."
                )},
            ],
        )
        print(f"\n--- Extract (200c): {result[:200]} ---")
        assert len(result) > 20

    @pytest.mark.asyncio
    async def test_embed(self):
        llm = _make_llm()
        vectors = await llm.embed(["Office laptop procurement contract"])
        assert len(vectors) == 1
        assert len(vectors[0]) > 100
        print(f"\n--- Embedding dim: {len(vectors[0])} ---")

    @pytest.mark.asyncio
    async def test_embed_batch(self):
        llm = _make_llm()
        vectors = await llm.embed(["ThinkPad laptop", "Dell monitor", "HP printer"])
        assert len(vectors) == 3
        assert all(len(v) == len(vectors[0]) for v in vectors)


# ── 3. Parser Registry ───────────────────────────────────────

class TestParserRegistry:
    def test_pdf_selection(self):
        reg = ParserRegistry(_make_llm())
        assert reg.get_parser("contract.pdf", "file").__class__.__name__ == "PDFParser"

    def test_excel_selection(self):
        reg = ParserRegistry(_make_llm())
        assert reg.get_parser("quotation.xlsx", "file").__class__.__name__ == "ExcelParser"

    def test_text_fallback(self):
        reg = ParserRegistry(_make_llm())
        assert reg.get_parser(None, "text").__class__.__name__ == "TextParser"

    def test_event_fallback(self):
        reg = ParserRegistry(_make_llm())
        assert reg.get_parser(None, "event").__class__.__name__ == "TextParser"

    def test_unknown_extension(self):
        reg = ParserRegistry(_make_llm())
        assert reg.get_parser("data.csv", "file").__class__.__name__ == "TextParser"

    @pytest.mark.asyncio
    async def test_text_parser_parse(self):
        reg = ParserRegistry(_make_llm())
        p = reg.get_parser(None, "text")
        result = await p.parse("Hello world test document.")
        assert isinstance(result, ParseResult)
        assert "Hello world" in result.text

    @pytest.mark.asyncio
    async def test_excel_parser_parse(self):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Product", "Price", "Qty"])
        ws.append(["ThinkPad X1", 9999, 5])
        ws.append(["Dell Monitor", 3999, 5])

        fd, path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        wb.save(path)

        try:
            reg = ParserRegistry(_make_llm())
            p = reg.get_parser("test.xlsx", "file")
            result = await p.parse(path)
            assert "ThinkPad" in result.text
            assert result.tables is not None
            print(f"\n--- Excel: {len(result.text)} chars, {len(result.tables)} tables ---")
        finally:
            os.unlink(path)


# ── 4. Ingestion Pipeline ────────────────────────────────────

@_skip_no_pg
class TestIngestionPipeline:
    @pytest.mark.asyncio
    async def test_text_ingestion(self):
        db = await _make_db()
        try:
            llm = _make_llm()
            repo = DataRepository(db.session_factory)
            pipeline = IngestionPipeline(
                parser_registry=ParserRegistry(llm),
                llm_service=llm, repo=repo, max_concurrent=5,
            )

            result = await pipeline.process(
                source_type="text",
                content_ref=(
                    "Contract HT-SMOKE-001. Party A: Smoke Corp. Party B: CSCEC. "
                    "Total: 99999 CNY. Items: 5x ThinkPad X1 Carbon. "
                    "Delivery: Shenzhen. Payment: 30 days."
                ),
                tenant_id=TENANT,
                metadata={"test": True},
            )

            print(f"\n--- Ingestion: status={result.status}, record={result.record_id} ---")
            assert result.status == "ready", f"Failed: {result.error}"
            assert result.source_id is not None
            assert result.record_id is not None
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_text_ingestion_with_schema(self):
        db = await _make_db()
        try:
            llm = _make_llm()
            repo = DataRepository(db.session_factory)
            pipeline = IngestionPipeline(
                parser_registry=ParserRegistry(llm),
                llm_service=llm, repo=repo, max_concurrent=5,
            )

            result = await pipeline.process(
                source_type="text",
                content_ref=(
                    "Quotation QT-001. Client: CSCEC. "
                    "ThinkPad x10 @ 9999 = 99990. Dell Monitor x10 @ 3500 = 35000. "
                    "Grand total: 134990 CNY."
                ),
                tenant_id=TENANT,
                schema_type="quotation",
                metadata={"test": True},
            )

            print(f"\n--- Quotation ingestion: {result.status} ---")
            assert result.status == "ready", f"Failed: {result.error}"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_excel_ingestion(self):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "PriceList"
        ws.append(["Product", "Spec", "Price", "Supplier"])
        ws.append(["ThinkPad X1", "i7/16G/512G", 9999, "JD"])
        ws.append(["Dell U2723QE", "27in 4K", 3999, "Dell"])

        fd, path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        wb.save(path)

        db = await _make_db()
        try:
            llm = _make_llm()
            repo = DataRepository(db.session_factory)
            pipeline = IngestionPipeline(
                parser_registry=ParserRegistry(llm),
                llm_service=llm, repo=repo, max_concurrent=5,
            )

            result = await pipeline.process(
                source_type="file", content_ref=path,
                file_name="price_list.xlsx", tenant_id=TENANT,
            )
            print(f"\n--- Excel ingestion: {result.status} ---")
            assert result.status == "ready", f"Failed: {result.error}"
        finally:
            os.unlink(path)
            await db.close()


# ── 5. Query Engine ──────────────────────────────────────────

@_skip_no_pg
class TestQueryEngine:
    @pytest.mark.asyncio
    async def test_sql_query(self):
        db = await _make_db()
        try:
            llm = _make_llm()
            repo = DataRepository(db.session_factory)
            engine = QueryEngine(repo=repo, llm_service=llm)

            results = await engine.query(
                intent="contract", mode="sql",
                filters={"schema_type": "contract"},
                tenant_id=TENANT, limit=5,
            )
            print(f"\n--- SQL query: {len(results)} results ---")
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_vector_query(self):
        db = await _make_db()
        try:
            llm = _make_llm()
            repo = DataRepository(db.session_factory)
            engine = QueryEngine(repo=repo, llm_service=llm)

            results = await engine.query(
                intent="laptop procurement contract",
                mode="vector", tenant_id=TENANT, limit=5,
            )
            print(f"\n--- Vector query: {len(results)} results ---")
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_hybrid_query(self):
        db = await _make_db()
        try:
            llm = _make_llm()
            repo = DataRepository(db.session_factory)
            engine = QueryEngine(repo=repo, llm_service=llm)

            results = await engine.query(
                intent="ThinkPad procurement",
                mode="hybrid", tenant_id=TENANT, limit=10,
            )
            print(f"\n--- Hybrid query: {len(results)} results ---")
            for r in results[:3]:
                print(f"  match={r.get('_match_type', 'N/A')}, type={r.get('schema_type', 'N/A')}")
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_record_crud(self):
        db = await _make_db()
        try:
            repo = DataRepository(db.session_factory)
            records = await repo.list_records(tenant_id=TENANT, limit=10)
            print(f"\n--- Records for {TENANT}: {len(records)} ---")

            if records:
                first = records[0]
                fetched = await repo.get_record(first.id)
                assert fetched is not None
                assert fetched.id == first.id
                print(f"  id={first.id}, type={first.schema_type}, status={first.status}")
        finally:
            await db.close()


# ── 6. Event Sink (real pipeline) ────────────────────────────

@_skip_no_pg
class TestEventSinkReal:
    @pytest.mark.asyncio
    async def test_file_uploaded_with_real_pipeline(self):
        import asyncio
        import fakeredis.aioredis
        from tonglu.services.event_sink import EventSinkListener, FILE_UPLOADED, FILE_READY

        db = await _make_db()
        try:
            llm = _make_llm()
            repo = DataRepository(db.session_factory)
            pipeline = IngestionPipeline(
                parser_registry=ParserRegistry(llm),
                llm_service=llm, repo=repo, max_concurrent=5,
            )

            redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
            session_id = str(uuid.uuid4())

            sink = EventSinkListener(
                redis_url="redis://fake", pipeline=pipeline,
                repo=repo, persist_rules=["*"], tenant_ids=[TENANT],
            )
            sink._redis = redis
            sink._running = True

            pubsub = redis.pubsub()
            await pubsub.subscribe(f"tempo:{TENANT}:events")

            event = {
                "id": str(uuid.uuid4()),
                "type": FILE_UPLOADED,
                "source": "agent_controller",
                "tenant_id": TENANT,
                "session_id": session_id,
                "payload": {
                    "file_id": "f_smoke",
                    "file_url": "text://Contract for 50 laptops total 499950 CNY",
                    "file_name": "summary.txt",
                },
            }

            await sink._handle_event(event)

            ready_msg = None
            for _ in range(20):
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg["type"] == "message":
                    ready_msg = json.loads(msg["data"])
                    break
                await asyncio.sleep(0.5)

            if ready_msg:
                print(f"\n--- FILE_READY payload ---")
                print(json.dumps(ready_msg.get("payload", {}), indent=2, ensure_ascii=False)[:500])
            else:
                print("\n--- No FILE_READY (pipeline may not support text:// URL scheme) ---")

            await pubsub.unsubscribe()
            await pubsub.aclose()
            await redis.aclose()
        finally:
            await db.close()


# ── 7. Session Evictor (archive + restore round-trip with real PG) ────

@_skip_no_pg
class TestSessionEvictorSmoke:
    @pytest.mark.asyncio
    async def test_archive_and_restore_roundtrip(self):
        """Full round-trip: populate Redis -> archive to PG -> clear Redis -> restore from PG."""
        import fakeredis.aioredis
        from tempo_os.memory.blackboard import TenantBlackboard
        from tempo_os.memory.chat_store import ChatStore, ChatMessage
        from tonglu.services.session_evictor import SessionEvictor

        db = await _make_db()
        try:
            repo = DataRepository(db.session_factory)
            redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
            tenant = f"evictor_smoke_{uuid.uuid4().hex[:4]}"
            session = f"sess_{uuid.uuid4().hex[:6]}"

            bb = TenantBlackboard(redis, tenant, session_ttl=1800)
            chat = ChatStore(redis, tenant, ttl=3600)

            # Populate Redis
            await bb.set_state(session, "project_name", "Smoke Test Project")
            await bb.set_state(session, "_routed_scene", "procurement")
            await chat.append(session, ChatMessage(role="user", content="Hello"))
            await chat.append(session, ChatMessage(role="assistant", content="Hi there!"))
            await bb.append_result(session, "search", {"q": "laptop", "results": 5})

            evictor = SessionEvictor(
                redis_url="redis://fake",
                repo=repo,
                tenant_ids=[tenant],
                scan_interval=60,
                ttl_threshold=300,
            )
            evictor._redis = redis

            # Archive
            archived = await evictor.archive_session(tenant, session)
            assert archived, "archive_session returned False"
            print(f"\n--- Archived session {session[:8]}... ---")

            # Verify PG snapshot
            snap = await repo.get_snapshot(session)
            assert snap is not None
            assert snap.tenant_id == tenant
            assert len(snap.chat_history) == 2
            assert snap.blackboard["project_name"] == "Smoke Test Project"
            assert snap.routed_scene == "procurement"
            print(f"  PG snapshot: chat={len(snap.chat_history)}, bb keys={list(snap.blackboard.keys())}")

            # Clear Redis (simulate expiration)
            from tempo_os.kernel.namespace import get_key, get_chat_key
            await redis.delete(get_key(tenant, "session", session))
            await redis.delete(get_chat_key(tenant, session))

            # Verify Redis is empty
            assert await chat.count(session) == 0
            assert await bb.get_state(session) == {}

            # Restore
            restored = await evictor.restore_session(
                tenant, session, session_ttl=1800, chat_ttl=3600
            )
            assert restored, "restore_session returned False"
            print(f"  Restored from PG")

            # Verify Redis repopulated
            count = await chat.count(session)
            assert count == 2, f"Expected 2 chat messages, got {count}"
            bb_state = await bb.get_state(session)
            assert bb_state.get("project_name") == "Smoke Test Project"
            assert bb_state.get("_routed_scene") == "procurement"
            print(f"  Redis: chat={count}, bb keys={list(bb_state.keys())}")

            # Verify PG marked as restored
            snap2 = await repo.get_snapshot(session)
            assert snap2.restored_at is not None
            print(f"  PG restored_at: {snap2.restored_at}")

            # Cleanup
            await repo.delete_snapshot(session)
            await redis.aclose()
            print("  *** EVICTOR ROUND-TRIP PASSED ***")
        finally:
            await db.close()
