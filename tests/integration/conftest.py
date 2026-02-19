# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Integration test fixtures — Real Redis + Real PostgreSQL.

These tests require running services:
  - Redis on localhost:6379
  - PostgreSQL on localhost:15432
"""

import uuid
import pytest
import redis.asyncio as aioredis

from tempo_os.core.config import settings
from tempo_os.kernel.redis_client import inject_redis_for_test
from tempo_os.core.context import init_platform_context
from tempo_os.storage.database import (
    get_engine, override_engine_for_test,
    create_all_tables, drop_all_tables, get_session_factory,
)
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# Import models so tables are registered
import tempo_os.storage.models  # noqa


@pytest.fixture(scope="session")
def event_loop():
    """Create a single event loop for the entire test session."""
    import asyncio
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def real_redis():
    """Connect to real Redis and flush test keys after each test."""
    r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    inject_redis_for_test(r)

    # Initialize platform context with real Redis
    ctx = init_platform_context(r)

    # Register nodes
    from tempo_os.nodes.echo import EchoNode
    from tempo_os.nodes.conditional import ConditionalNode
    from tempo_os.nodes.transform import TransformNode
    from tempo_os.nodes.http_request import HTTPRequestNode
    from tempo_os.nodes.notification import NotificationNode

    ctx.node_registry.register_builtin("echo", EchoNode())
    ctx.node_registry.register_builtin("conditional", ConditionalNode())
    ctx.node_registry.register_builtin("transform", TransformNode())
    ctx.node_registry.register_builtin("http_request", HTTPRequestNode())
    ctx.node_registry.register_builtin("notification", NotificationNode())

    # Load flows
    from pathlib import Path
    from tempo_os.kernel.flow_loader import load_flow_from_yaml
    flows_dir = Path(__file__).parent.parent.parent / "flows" / "examples"
    if flows_dir.exists():
        for f in flows_dir.glob("*.yaml"):
            try:
                flow_def = load_flow_from_yaml(f)
                ctx.register_flow(flow_def.name, flow_def)
            except Exception:
                pass

    yield r

    # Cleanup: flush all tempo:* keys
    async for key in r.scan_iter(match="tempo:*"):
        await r.delete(key)
    await r.aclose()


@pytest.fixture
async def real_db():
    """Create real PG tables, yield session factory, drop after test."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    override_engine_for_test(engine)

    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(tempo_os.storage.models.Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    yield factory

    # Drop tables after test
    async with engine.begin() as conn:
        await conn.run_sync(tempo_os.storage.models.Base.metadata.drop_all)

    await engine.dispose()


import tempo_os.storage.models
