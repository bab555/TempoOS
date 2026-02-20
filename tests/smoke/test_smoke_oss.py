# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Smoke test -- OSS post-signature endpoint with real config.

Verifies that:
  - Signature is generated with real OSS credentials
  - Object key follows the expected pattern
  - Upload URL is well-formed

Does NOT actually upload to OSS (that requires a browser/frontend).

Run:  pytest tests/smoke/test_smoke_oss.py -v -s --timeout=30
"""

import json

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from tempo_os.api.oss import router as oss_router
from tempo_os.api.deps import get_current_tenant
from tempo_os.core.config import settings
from tempo_os.core.tenant import TenantContext

pytestmark = pytest.mark.skipif(
    not (settings.OSS_ENDPOINT and settings.OSS_BUCKET and settings.OSS_ACCESS_KEY_ID and settings.OSS_ACCESS_KEY_SECRET),
    reason="OSS not fully configured",
)


def _make_oss_app() -> FastAPI:
    app = FastAPI()
    app.include_router(oss_router, prefix="/api")
    app.dependency_overrides[get_current_tenant] = lambda: TenantContext(
        tenant_id="smoke_oss", user_id="smoke_user_001"
    )
    return app


class TestOssSignatureReal:
    @pytest.mark.asyncio
    async def test_xlsx_signature(self):
        app = _make_oss_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t", timeout=10) as c:
            resp = await c.post("/api/oss/post-signature", json={
                "filename": "quotation_template.xlsx",
                "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            })

        assert resp.status_code == 200
        data = resp.json()
        print(f"\n--- OSS Signature Response ---")
        print(json.dumps(data, indent=2))

        upload = data["upload"]
        assert upload["method"] == "POST"
        assert settings.OSS_BUCKET in upload["url"]
        assert "policy" in upload["fields"]
        assert "signature" in upload["fields"]
        assert upload["fields"]["OSSAccessKeyId"] == settings.OSS_ACCESS_KEY_ID

        obj = data["object"]
        assert obj["bucket"] == settings.OSS_BUCKET
        assert "quotation_template.xlsx" in obj["key"]
        assert obj["key"].startswith("tempoos/tenant/smoke_oss/user/smoke_user_001/")
        assert obj["url"].startswith("https://")

    @pytest.mark.asyncio
    async def test_pdf_with_custom_dir(self):
        app = _make_oss_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t", timeout=10) as c:
            resp = await c.post("/api/oss/post-signature", json={
                "filename": "contract.pdf",
                "dir": "templates/contracts/",
                "expire_seconds": 300,
            })

        assert resp.status_code == 200
        obj = resp.json()["object"]
        assert "/templates/contracts/" in obj["key"]
        assert "contract.pdf" in obj["key"]

    @pytest.mark.asyncio
    async def test_signature_fields_are_valid_base64(self):
        import base64
        app = _make_oss_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t", timeout=10) as c:
            resp = await c.post("/api/oss/post-signature", json={"filename": "test.txt"})

        fields = resp.json()["upload"]["fields"]
        # policy and signature should be valid base64
        base64.b64decode(fields["policy"])
        base64.b64decode(fields["signature"])
        print("  Base64 validation: OK")

    @pytest.mark.asyncio
    async def test_anonymous_user_path(self):
        """When user_id is None, path should use 'anonymous'."""
        app = FastAPI()
        app.include_router(oss_router, prefix="/api")
        app.dependency_overrides[get_current_tenant] = lambda: TenantContext(
            tenant_id="t_anon", user_id=None
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t", timeout=10) as c:
            resp = await c.post("/api/oss/post-signature", json={"filename": "file.pdf"})

        assert resp.status_code == 200
        key = resp.json()["object"]["key"]
        assert "/user/anonymous/" in key
