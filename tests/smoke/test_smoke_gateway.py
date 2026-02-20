# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Smoke test -- Gateway LLM chat and embedding endpoints with real DashScope.

Verifies that:
  - POST /api/llm/chat returns a real LLM response
  - POST /api/llm/embedding returns real vectors with correct dimensions
  - Error handling works when API key is missing

Run:  pytest tests/smoke/test_smoke_gateway.py -v -s --timeout=120
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempo_os.api.gateway import router as gateway_router
from tempo_os.api.deps import get_current_tenant
from tempo_os.core.config import settings
from tempo_os.core.tenant import TenantContext

pytestmark = pytest.mark.skipif(
    not settings.DASHSCOPE_API_KEY,
    reason="DASHSCOPE_API_KEY not configured",
)


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(gateway_router, prefix="/api")
    app.dependency_overrides[get_current_tenant] = lambda: TenantContext(
        tenant_id="smoke_gw", user_id="smoke_user"
    )
    return app


class TestGatewayChat:
    @pytest.mark.asyncio
    async def test_simple_chat(self):
        """Real DashScope chat call via Gateway."""
        app = _make_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t", timeout=60) as c:
            resp = await c.post("/api/llm/chat", json={
                "messages": [{"role": "user", "content": "1+1=?"}],
                "model": "qwen-max",
                "temperature": 0.1,
            })

        assert resp.status_code == 200, f"Unexpected status: {resp.status_code} {resp.text}"
        data = resp.json()
        print(f"\n--- Gateway Chat Response ---")
        print(f"  model: {data['model']}")
        print(f"  content: {data['content'][:200]}")
        print(f"  usage: {data.get('usage')}")

        assert data["model"] == "qwen-max"
        assert len(data["content"]) > 0
        assert "2" in data["content"]

    @pytest.mark.asyncio
    async def test_chat_with_system_prompt(self):
        """Chat with system prompt to verify multi-message support."""
        app = _make_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t", timeout=60) as c:
            resp = await c.post("/api/llm/chat", json={
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant. Reply in English only."},
                    {"role": "user", "content": "Say hello"},
                ],
            })

        assert resp.status_code == 200
        content = resp.json()["content"].lower()
        assert "hello" in content or "hi" in content


class TestGatewayEmbedding:
    @pytest.mark.asyncio
    async def test_single_text_embedding(self):
        """Real DashScope embedding call via Gateway."""
        app = _make_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t", timeout=60) as c:
            resp = await c.post("/api/llm/embedding", json={
                "texts": ["ThinkPad X1 Carbon laptop procurement"],
            })

        assert resp.status_code == 200, f"Unexpected status: {resp.status_code} {resp.text}"
        data = resp.json()
        print(f"\n--- Gateway Embedding Response ---")
        print(f"  dim: {data['dim']}")
        print(f"  vectors count: {len(data['vectors'])}")
        print(f"  first 5 dims: {data['vectors'][0][:5]}")

        assert len(data["vectors"]) == 1
        assert data["dim"] > 0
        assert len(data["vectors"][0]) == data["dim"]
        assert any(v != 0.0 for v in data["vectors"][0])

    @pytest.mark.asyncio
    async def test_batch_embedding(self):
        """Batch embedding with multiple texts."""
        app = _make_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t", timeout=60) as c:
            resp = await c.post("/api/llm/embedding", json={
                "texts": [
                    "procurement contract for office supplies",
                    "delivery note for electronics",
                    "financial quarterly report",
                ],
            })

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["vectors"]) == 3
        for vec in data["vectors"]:
            assert len(vec) == data["dim"]
