# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
TempoOS Application Entry Point.

FastAPI app with lifespan, middleware, all API routers,
and automatic builtin node registration.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tempo_os.kernel.redis_client import get_redis_pool, close_redis_pool
from tempo_os.core.context import init_platform_context, get_platform_context
from tempo_os.core.metrics import platform_metrics
from tempo_os.api.errors import APIError, api_error_handler
from tempo_os.api.middleware import TraceMiddleware
from tempo_os.api.workflow import router as workflow_router
from tempo_os.api.registry_api import router as registry_router
from tempo_os.api.state import router as state_router
from tempo_os.api.gateway import router as gateway_router
from tempo_os.api.ws import router as ws_router
from tempo_os.api.observability import router as observability_router
from tempo_os.api.agent import router as agent_router
from tempo_os.api.oss import router as oss_router

# Built-in nodes
from tempo_os.nodes.echo import EchoNode
from tempo_os.nodes.conditional import ConditionalNode
from tempo_os.nodes.transform import TransformNode
from tempo_os.nodes.http_request import HTTPRequestNode
from tempo_os.nodes.notification import NotificationNode
from tempo_os.nodes.search import SearchNode
from tempo_os.nodes.writer import WriterNode

# Tonglu data nodes
from tempo_os.nodes.data_query import DataQueryNode
from tempo_os.nodes.data_ingest import DataIngestNode
from tempo_os.nodes.file_parser import FileParserNode
from tempo_os.runtime.tonglu_client import TongluClient

# Flow loader
from tempo_os.kernel.flow_loader import load_flow_from_yaml

logger = logging.getLogger("tempo.main")


def _register_builtin_nodes(ctx) -> None:
    """Register all built-in platform nodes."""
    import os

    nodes = [
        EchoNode(),
        ConditionalNode(),
        TransformNode(),
        HTTPRequestNode(),
        NotificationNode(),
        SearchNode(),
        WriterNode(),
    ]

    # Tonglu data nodes (only if TONGLU_URL is configured)
    tonglu_url = os.getenv("TONGLU_URL", "http://localhost:8100")
    tonglu_client = TongluClient(base_url=tonglu_url)
    nodes.extend([
        DataQueryNode(tonglu_client),
        DataIngestNode(tonglu_client),
        FileParserNode(tonglu_client),
    ])

    for node in nodes:
        ctx.node_registry.register_builtin(node.node_id, node)
    platform_metrics.set_gauge("nodes_registered", len(ctx.node_registry))
    logger.info("Registered %d builtin nodes (including Tonglu data nodes)", len(nodes))


def _load_example_flows(ctx) -> None:
    """Load YAML flows from flows/examples/ directory."""
    flows_dir = Path(__file__).parent.parent / "flows" / "examples"
    if not flows_dir.exists():
        return
    for yaml_file in flows_dir.glob("*.yaml"):
        try:
            flow_def = load_flow_from_yaml(yaml_file)
            errors = ctx.register_flow(flow_def.name, flow_def)
            if errors:
                logger.warning("Flow '%s' has validation warnings: %s", flow_def.name, errors)
            else:
                logger.info("Loaded flow: %s", flow_def.name)
        except Exception as e:
            logger.error("Failed to load flow %s: %s", yaml_file, e)
    platform_metrics.set_gauge("flows_registered", len(ctx.list_flows()))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup/shutdown of platform resources."""
    # Startup
    redis = await get_redis_pool()
    ctx = init_platform_context(redis)
    _register_builtin_nodes(ctx)
    _load_example_flows(ctx)
    logger.info("[TempoOS] Platform ready")
    yield
    # Shutdown
    await close_redis_pool()
    logger.info("[TempoOS] Shutdown complete")


app = FastAPI(
    title="TempoOS",
    description="Digital Employee Workflow Platform",
    version="0.1.0",
    lifespan=lifespan,
)

# ── Middleware ───────────────────────────────────────────────
app.add_middleware(TraceMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Error Handlers ──────────────────────────────────────────
app.add_exception_handler(APIError, api_error_handler)

# ── Routes ──────────────────────────────────────────────────
app.include_router(workflow_router, prefix="/api")
app.include_router(registry_router, prefix="/api")
app.include_router(state_router, prefix="/api")
app.include_router(gateway_router, prefix="/api")
app.include_router(agent_router, prefix="/api")
app.include_router(oss_router, prefix="/api")
app.include_router(ws_router)
app.include_router(observability_router)
