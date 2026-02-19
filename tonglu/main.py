# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Tonglu FastAPI Application — TempoOS Data Service Layer.

Entry point: uvicorn tonglu.main:app --host 0.0.0.0 --port 8100
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tonglu.api.ingest import router as ingest_router
from tonglu.api.query import router as query_router
from tonglu.api.tasks import router as tasks_router
from tonglu.api.oss_callback import router as oss_callback_router
from tonglu.config import TongluSettings
from tonglu.parsers.registry import ParserRegistry
from tonglu.pipeline.ingestion import IngestionPipeline
from tonglu.query.engine import QueryEngine
from tonglu.services.event_sink import EventSinkListener
from tonglu.services.llm_service import LLMService
from tonglu.storage.database import Database
from tonglu.storage.repositories import DataRepository

logger = logging.getLogger("tonglu")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown hooks."""
    settings = TongluSettings()

    # ── Startup ───────────────────────────────────────────────
    logger.info("Tonglu starting up...")

    # Database
    db = Database(settings.DATABASE_URL)
    await db.init()
    app.state.db = db

    # Repository
    repo = DataRepository(db.session_factory)
    app.state.repo = repo

    # LLM Service
    llm = LLMService(
        api_key=settings.DASHSCOPE_API_KEY,
        default_model=settings.DASHSCOPE_DEFAULT_MODEL,
        embedding_model=settings.DASHSCOPE_EMBEDDING_MODEL,
    )
    app.state.llm = llm

    # Parser Registry
    parser_registry = ParserRegistry(llm)

    # Ingestion Pipeline (20 concurrent)
    pipeline = IngestionPipeline(
        parser_registry=parser_registry,
        llm_service=llm,
        repo=repo,
        max_concurrent=settings.INGESTION_MAX_CONCURRENT,
    )
    app.state.pipeline = pipeline

    # Query Engine
    query_engine = QueryEngine(repo=repo, llm_service=llm)
    app.state.query_engine = query_engine

    # Store settings
    app.state.settings = settings

    # Event Sink (optional)
    event_sink = None
    if settings.EVENT_SINK_ENABLED:
        event_sink = EventSinkListener(
            redis_url=settings.REDIS_URL,
            pipeline=pipeline,
            repo=repo,
            persist_rules=settings.persist_rules_list,
            tenant_ids=settings.tenant_ids_list,
        )
        await event_sink.start()
        app.state.event_sink = event_sink

    logger.info(
        "Tonglu ready — host=%s port=%d db=%s concurrent=%d event_sink=%s",
        settings.HOST,
        settings.PORT,
        settings.DATABASE_URL.split("@")[-1] if "@" in settings.DATABASE_URL else "***",
        settings.INGESTION_MAX_CONCURRENT,
        "enabled" if event_sink else "disabled",
    )

    yield

    # ── Shutdown ──────────────────────────────────────────────
    logger.info("Tonglu shutting down...")

    if event_sink:
        await event_sink.stop()

    await db.close()
    logger.info("Tonglu stopped.")


# ── App ───────────────────────────────────────────────────────

app = FastAPI(
    title="铜炉 Tonglu",
    description="TempoOS Data Service Layer — 智能 CRM 数据中台",
    version="2.0.0",
    lifespan=lifespan,
)


@app.get("/health", tags=["system"])
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "tonglu", "version": "2.0.0"}


# ── API Routers ───────────────────────────────────────────────

app.include_router(ingest_router)
app.include_router(query_router)
app.include_router(tasks_router)
app.include_router(oss_callback_router)
