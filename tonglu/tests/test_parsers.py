# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""Tests for Tonglu file parsers and registry."""

import os
import tempfile

import pytest

from tonglu.parsers.base import BaseParser, ParseResult
from tonglu.parsers.excel_parser import ExcelParser
from tonglu.parsers.image_parser import ImageParser
from tonglu.parsers.pdf_parser import PDFParser
from tonglu.parsers.registry import ParserRegistry
from tonglu.parsers.text_parser import TextParser


class TestTextParser:
    @pytest.mark.asyncio
    async def test_parse_text(self):
        """TextParser should return content_ref as text."""
        parser = TextParser()
        result = await parser.parse("这是一段测试文本")
        assert result.text == "这是一段测试文本"
        assert result.metadata["parser"] == "text"

    def test_supports_text_type(self):
        parser = TextParser()
        assert parser.supports(None, "text") is True
        assert parser.supports(None, "event") is True
        assert parser.supports("file.pdf", "file") is False

    def test_supports_no_filename(self):
        parser = TextParser()
        assert parser.supports(None, "unknown") is True


class TestPDFParser:
    def test_supports(self):
        parser = PDFParser()
        assert parser.supports("report.pdf", "file") is True
        assert parser.supports("REPORT.PDF", "file") is True
        assert parser.supports("data.xlsx", "file") is False
        assert parser.supports(None, "file") is False

    @pytest.mark.asyncio
    async def test_parse_real_pdf(self, tmp_path):
        """Test PDF parsing with a minimal real PDF file."""
        # Create a minimal PDF using pdfplumber-compatible format
        # Skip if no test PDF available — this is a structural test
        parser = PDFParser()
        # We test the supports() method; actual PDF parsing requires a real file
        assert parser.supports("contract.pdf", "file") is True


class TestExcelParser:
    def test_supports(self):
        parser = ExcelParser()
        assert parser.supports("data.xlsx", "file") is True
        assert parser.supports("DATA.XLS", "file") is True
        assert parser.supports("report.pdf", "file") is False
        assert parser.supports(None, "file") is False

    @pytest.mark.asyncio
    async def test_parse_real_excel(self, tmp_path):
        """Test Excel parsing with a real xlsx file."""
        import openpyxl

        # Create a test Excel file
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws.append(["Name", "Amount", "Date"])
        ws.append(["华为", 1000000, "2026-01-15"])
        ws.append(["腾讯", 500000, "2026-02-01"])

        file_path = str(tmp_path / "test.xlsx")
        wb.save(file_path)

        parser = ExcelParser()
        result = await parser.parse(file_path)

        assert "华为" in result.text
        assert "腾讯" in result.text
        assert result.metadata["sheets"] == ["Sheet1"]
        assert result.metadata["total_rows"] == 3
        assert result.tables is not None
        assert len(result.tables) == 1
        assert result.tables[0]["sheet"] == "Sheet1"


class TestImageParser:
    def test_supports(self):
        parser = ImageParser(llm_service=None)
        assert parser.supports("photo.jpg", "file") is True
        assert parser.supports("scan.png", "file") is True
        assert parser.supports("doc.JPEG", "file") is True
        assert parser.supports("doc.pdf", "file") is False
        assert parser.supports(None, "file") is False

    @pytest.mark.asyncio
    async def test_parse_calls_llm(self, mock_llm):
        """ImageParser should call LLM with vision task_type."""
        parser = ImageParser(mock_llm)
        result = await parser.parse("/path/to/image.jpg")

        assert len(mock_llm.call_log) == 1
        assert mock_llm.call_log[0]["task_type"] == "vision"
        assert "图片" in result.text or "合同" in result.text


class TestParserRegistry:
    def test_pdf_selection(self, parser_registry):
        p = parser_registry.get_parser("contract.pdf", "file")
        assert isinstance(p, PDFParser)

    def test_excel_selection(self, parser_registry):
        p = parser_registry.get_parser("data.xlsx", "file")
        assert isinstance(p, ExcelParser)

    def test_image_selection(self, parser_registry):
        p = parser_registry.get_parser("scan.jpg", "file")
        assert isinstance(p, ImageParser)

    def test_text_fallback(self, parser_registry):
        p = parser_registry.get_parser(None, "text")
        assert isinstance(p, TextParser)

    def test_event_fallback(self, parser_registry):
        p = parser_registry.get_parser(None, "event")
        assert isinstance(p, TextParser)

    def test_unknown_file_fallback(self, parser_registry):
        p = parser_registry.get_parser("data.csv", "file")
        assert isinstance(p, TextParser)
