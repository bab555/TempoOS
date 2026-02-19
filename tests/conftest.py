# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Shared test fixtures for all TempoOS tests.
"""

import uuid

import pytest
import fakeredis.aioredis

from tempo_os.kernel.redis_client import inject_redis_for_test
from tempo_os.core.context import init_platform_context


@pytest.fixture
def mock_redis():
    """Provide a FakeRedis async instance and initialize PlatformContext."""
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    inject_redis_for_test(r)

    # Initialize PlatformContext with FakeRedis
    # This ensures API routes can call get_platform_context()
    ctx = init_platform_context(r)

    # Register builtin nodes (same as main.py startup)
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

    # Load example flows
    from pathlib import Path
    from tempo_os.kernel.flow_loader import load_flow_from_yaml

    flows_dir = Path(__file__).parent.parent / "flows" / "examples"
    if flows_dir.exists():
        for yaml_file in flows_dir.glob("*.yaml"):
            try:
                flow_def = load_flow_from_yaml(yaml_file)
                ctx.register_flow(flow_def.name, flow_def)
            except Exception:
                pass

    return r


@pytest.fixture
def mock_tenant_id() -> str:
    """Provide a test tenant ID."""
    return "test_tenant"


@pytest.fixture
def mock_session_id() -> str:
    """Provide a test session ID."""
    return str(uuid.uuid4())
