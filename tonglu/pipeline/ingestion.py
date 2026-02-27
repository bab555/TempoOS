# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Ingestion Pipeline — Core data processing flow.

Flow: Save Raw → Parse → LLM Type Detection → LLM Field Extraction → Embed → Persist

Concurrency is controlled by asyncio.Semaphore (default 20).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from uuid import UUID

from tonglu.parsers.registry import ParserRegistry
from tonglu.services.llm_service import LLMService
from tonglu.storage.models import DataRecord, DataSource, DataVector
from tonglu.storage.repositories import DataRepository

logger = logging.getLogger("tonglu.pipeline")


@dataclass
class IngestionResult:
    """摄入结果。"""
    source_id: Optional[UUID] = None
    record_id: Optional[UUID] = None
    status: str = "processing"  # "ready" / "error"
    error: Optional[str] = None


class IngestionPipeline:
    """
    数据摄入流水线 — 20 并发控制。

    每条数据经过 6 步处理：
    1. 保存原始数据 (DataSource)
    2. 解析内容 (Parser)
    3. LLM 识别类型 (如果未指定 schema_type)
    4. LLM 字段提取 + 摘要
    5. 向量化 (Embedding)
    6. 持久化 (DataRecord + DataVector)
    """

    def __init__(
        self,
        parser_registry: ParserRegistry,
        llm_service: LLMService,
        repo: DataRepository,
        max_concurrent: int = 20,
    ) -> None:
        self._parsers = parser_registry
        self._llm = llm_service
        self._repo = repo
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def process(
        self,
        source_type: str,
        content_ref: str,
        file_name: Optional[str] = None,
        tenant_id: str = "default",
        schema_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> IngestionResult:
        """
        Process a single data item through the full pipeline.

        Args:
            source_type: "file" / "text" / "url" / "event"
            content_ref: File path or text content.
            file_name: Original file name (for parser selection).
            tenant_id: Tenant scope.
            schema_type: Data type (auto-detected if None).
            metadata: Additional metadata to store.

        Returns:
            IngestionResult with source_id, record_id, and status.
        """
        async with self._semaphore:
            source: Optional[DataSource] = None
            try:
                # Step 1: 保存原始数据
                source = DataSource(
                    tenant_id=tenant_id,
                    source_type=source_type,
                    file_name=file_name,
                    content_ref=content_ref,
                    metadata_=metadata or {},
                )
                source = await self._repo.save_source(source)
                logger.info(
                    "Ingestion started: source_id=%s type=%s file=%s",
                    source.id, source_type, file_name,
                )

                # Step 1.5: Download URL content if source is a remote URL
                actual_ref = content_ref
                tmp_path: Optional[str] = None
                if source_type == "url" and content_ref.startswith("http"):
                    actual_ref, tmp_path = await self._download_url(content_ref, file_name)

                # Step 2: 解析内容
                try:
                    parser = self._parsers.get_parser(file_name, source_type)
                    parse_result = await parser.parse(actual_ref)
                finally:
                    if tmp_path and os.path.exists(tmp_path):
                        os.unlink(tmp_path)

                if not parse_result.text.strip():
                    raise ValueError("Parser returned empty text")

                # Step 3: LLM 识别类型（如果未指定）
                if not schema_type:
                    schema_type = await self._detect_type(parse_result.text)
                    logger.debug("Auto-detected schema_type: %s", schema_type)

                # Step 4: LLM 字段提取 + 摘要
                extracted = await self._extract_fields(parse_result.text, schema_type)

                # Step 5: 向量化
                summary_text = extracted.get("summary", "")
                if summary_text:
                    embeddings = await self._llm.embed([summary_text])
                    embedding_vector = embeddings[0]
                else:
                    embedding_vector = None

                # Step 6: 持久化
                record = DataRecord(
                    tenant_id=tenant_id,
                    source_id=source.id,
                    schema_type=schema_type,
                    data=extracted.get("fields", {}),
                    summary=summary_text,
                    status="ready",
                    processing_log=[
                        f"parsed:{parser.__class__.__name__}",
                        f"schema:{schema_type}",
                        f"fields:{len(extracted.get('fields', {}))}",
                    ],
                )
                record = await self._repo.save_record(record)

                if embedding_vector:
                    await self._repo.save_vectors([
                        DataVector(
                            record_id=record.id,
                            chunk_content=summary_text,
                            embedding=embedding_vector,
                        )
                    ])

                logger.info(
                    "Ingestion complete: source_id=%s record_id=%s schema=%s",
                    source.id, record.id, schema_type,
                )

                return IngestionResult(
                    source_id=source.id,
                    record_id=record.id,
                    status="ready",
                )

            except Exception as e:
                logger.error(
                    "Ingestion failed: source_id=%s error=%s",
                    source.id if source else "N/A", e,
                    exc_info=True,
                )
                # Try to mark the record as error if we have a source
                if source:
                    try:
                        error_record = DataRecord(
                            tenant_id=tenant_id,
                            source_id=source.id,
                            schema_type=schema_type or "unknown",
                            data={},
                            summary="",
                            status="error",
                            processing_log=[f"error:{str(e)}"],
                        )
                        error_record = await self._repo.save_record(error_record)
                    except Exception:
                        logger.error("Failed to save error record", exc_info=True)

                return IngestionResult(
                    source_id=source.id if source else None,
                    record_id=None,
                    status="error",
                    error=str(e),
                )

    async def _download_url(self, url: str, file_name: Optional[str] = None) -> tuple[str, str]:
        """
        Download a URL (typically OSS) to a temp file.

        For private OSS buckets, uses oss2 SDK with signed URL.
        Returns (local_path_or_text, tmp_path).
        """
        parsed = urlparse(url)
        is_oss = "aliyuncs.com" in (parsed.hostname or "")

        if is_oss:
            return await asyncio.to_thread(self._oss_download_sync, url, file_name)

        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            suffix = os.path.splitext(file_name or "file.txt")[1] or ".txt"
            fd, tmp_path = tempfile.mkstemp(suffix=suffix)
            os.write(fd, resp.content)
            os.close(fd)
            logger.info("Downloaded URL to %s (%d bytes)", tmp_path, len(resp.content))

            if suffix in (".txt", ".md", ".csv", ".json", ".log"):
                text = resp.content.decode("utf-8", errors="replace")
                os.unlink(tmp_path)
                return text, ""
            return tmp_path, tmp_path

    @staticmethod
    def _oss_download_sync(url: str, file_name: Optional[str] = None) -> tuple[str, str]:
        """Synchronous OSS download using oss2 SDK (runs in thread)."""
        import oss2
        from tonglu.config import TongluSettings

        settings = TongluSettings()

        parsed = urlparse(url)
        host = parsed.hostname or ""
        bucket_name = host.split(".")[0]
        object_key = parsed.path.lstrip("/")

        endpoint = "https://" + host.replace(f"{bucket_name}.", "")

        auth = oss2.Auth(settings.OSS_ACCESS_KEY_ID, settings.OSS_ACCESS_KEY_SECRET)
        bucket = oss2.Bucket(auth, endpoint, bucket_name)

        result = bucket.get_object(object_key)
        content = result.read()
        logger.info("OSS download: %s (%d bytes)", object_key, len(content))

        suffix = os.path.splitext(file_name or object_key)[1] or ".txt"
        if suffix in (".txt", ".md", ".csv", ".json", ".log"):
            return content.decode("utf-8", errors="replace"), ""

        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        os.write(fd, content)
        os.close(fd)
        return tmp_path, tmp_path

    async def process_batch(
        self, items: List[Dict[str, Any]],
    ) -> List[IngestionResult]:
        """
        Batch processing — all items share the same Semaphore.

        Args:
            items: List of dicts, each containing process() kwargs.

        Returns:
            List of IngestionResult (one per item, in order).
        """
        tasks = [self.process(**item) for item in items]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Convert exceptions to IngestionResult
        final: List[IngestionResult] = []
        for r in results:
            if isinstance(r, Exception):
                final.append(IngestionResult(status="error", error=str(r)))
            else:
                final.append(r)
        return final

    # ── Internal LLM Helpers ──────────────────────────────────

    async def _detect_type(self, text: str) -> str:
        """LLM 识别数据类型。"""
        messages = [
            {
                "role": "user",
                "content": (
                    "判断以下文本属于哪种业务数据类型。"
                    "只返回类型名称，可选值：invoice, contract, contact, "
                    "quotation, meeting_note, report, other\n\n"
                    + text[:500]
                ),
            }
        ]
        result = await self._llm.call(task_type="route", messages=messages)
        return result.strip().lower()

    async def _extract_fields(self, text: str, schema_type: str) -> Dict[str, Any]:
        """LLM 提取字段 + 生成摘要。"""
        messages = [
            {
                "role": "system",
                "content": (
                    f"你是一个数据提取专家。从文本中提取 {schema_type} 类型的关键字段，"
                    f"并生成一段 50 字以内的摘要。"
                    f'返回 JSON 格式：{{"fields": {{...}}, "summary": "..."}}'
                ),
            },
            {
                "role": "user",
                "content": text[:3000],  # 截断防止 token 过多
            },
        ]
        result = await self._llm.call(task_type="extract", messages=messages)

        # Parse JSON response — handle potential markdown code blocks
        cleaned = result.strip()
        if cleaned.startswith("```"):
            # Remove markdown code block wrapper
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("LLM returned non-JSON response, wrapping as fields")
            parsed = {"fields": {"raw_text": cleaned}, "summary": cleaned[:50]}

        # Ensure required keys exist
        if "fields" not in parsed:
            parsed["fields"] = {}
        if "summary" not in parsed:
            parsed["summary"] = str(parsed.get("fields", ""))[:50]

        return parsed
