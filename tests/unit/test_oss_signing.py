# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""Unit tests for OSS post-signature endpoint."""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from tempo_os.api.oss import router as oss_router
from tempo_os.api.deps import get_current_tenant
from tempo_os.core.tenant import TenantContext

app = FastAPI()
app.include_router(oss_router, prefix="/api")


def _make_tenant(tenant_id: str = "test", user_id: str = "u1") -> TenantContext:
    return TenantContext(tenant_id=tenant_id, user_id=user_id)


app.dependency_overrides[get_current_tenant] = lambda: _make_tenant()


class TestOssPostSignature:
    @pytest.mark.asyncio
    async def test_missing_config_returns_501(self):
        from unittest.mock import patch

        with patch("tempo_os.api.oss.settings") as ms:
            ms.OSS_ENDPOINT = ""
            ms.OSS_BUCKET = ""
            ms.OSS_ACCESS_KEY_ID = ""
            ms.OSS_ACCESS_KEY_SECRET = ""

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/oss/post-signature", json={
                    "filename": "test.xlsx",
                })
            assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_success_returns_signed_fields(self):
        from unittest.mock import patch

        with patch("tempo_os.api.oss.settings") as ms:
            ms.OSS_ENDPOINT = "oss-cn-hangzhou.aliyuncs.com"
            ms.OSS_BUCKET = "test-bucket"
            ms.OSS_ACCESS_KEY_ID = "LTAI_test_key"
            ms.OSS_ACCESS_KEY_SECRET = "secret_key_for_testing"
            ms.OSS_UPLOAD_PREFIX = "tempoos"
            ms.OSS_MAX_UPLOAD_SIZE = 200 * 1024 * 1024

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/oss/post-signature", json={
                    "filename": "report.docx",
                    "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "expire_seconds": 300,
                })

            assert resp.status_code == 200
            data = resp.json()

            assert "upload" in data
            assert "object" in data

            upload = data["upload"]
            assert upload["method"] == "POST"
            assert "test-bucket" in upload["url"]
            assert "policy" in upload["fields"]
            assert "signature" in upload["fields"]
            assert "OSSAccessKeyId" in upload["fields"]
            assert upload["fields"]["OSSAccessKeyId"] == "LTAI_test_key"

            obj = data["object"]
            assert obj["bucket"] == "test-bucket"
            assert "report.docx" in obj["key"]
            assert obj["key"].startswith("tempoos/tenant/test/")
            assert "report.docx" in obj["url"]

    @pytest.mark.asyncio
    async def test_custom_dir(self):
        from unittest.mock import patch

        with patch("tempo_os.api.oss.settings") as ms:
            ms.OSS_ENDPOINT = "oss-cn-hangzhou.aliyuncs.com"
            ms.OSS_BUCKET = "test-bucket"
            ms.OSS_ACCESS_KEY_ID = "key"
            ms.OSS_ACCESS_KEY_SECRET = "secret"
            ms.OSS_UPLOAD_PREFIX = "tempoos"
            ms.OSS_MAX_UPLOAD_SIZE = 200 * 1024 * 1024

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/oss/post-signature", json={
                    "filename": "template.xlsx",
                    "dir": "templates/",
                })

            assert resp.status_code == 200
            key = resp.json()["object"]["key"]
            assert "/templates/" in key
