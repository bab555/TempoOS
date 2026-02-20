# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Smoke test -- Real DashScope document generation via WriterNode.
Covers ALL 6 skill types + Blackboard context relay.

Requires:
  - DASHSCOPE_API_KEY in environment / .env
  - Network access to DashScope API

Run:  pytest tests/smoke/test_smoke_writer.py -v -s --timeout=180
"""

import json

import pytest

from tempo_os.core.config import settings
from tempo_os.memory.blackboard import TenantBlackboard
from tempo_os.nodes.writer import WriterNode

pytestmark = pytest.mark.skipif(
    not settings.DASHSCOPE_API_KEY,
    reason="DASHSCOPE_API_KEY not configured",
)


class TestWriterQuotation:
    @pytest.mark.asyncio
    async def test_quotation(self, mock_redis):
        node = WriterNode()
        bb = TenantBlackboard(mock_redis, "smoke")

        result = await node.execute("w_qt", "smoke", {
            "skill": "quotation",
            "data": {
                "project_name": "Office Equipment Procurement",
                "client": "CSCEC 4th Bureau",
                "items": [
                    {"name": "ThinkPad X1 Carbon", "spec": "i7-13700H/16G/512G", "qty": 5, "unit_price": 9999},
                    {"name": "Dell U2723QE Monitor", "spec": "27in 4K IPS", "qty": 5, "unit_price": 3999},
                    {"name": "Logitech MX Master 3S", "spec": "Wireless Mouse", "qty": 10, "unit_price": 699},
                ],
            },
        }, bb)

        print(f"\n--- Quotation: {result.status} ---")
        print(json.dumps(result.result, ensure_ascii=False, indent=2)[:600])

        assert result.is_success, f"Failed: {result.error_message}"
        assert result.result.get("type") in ("table", "document")
        assert result.ui_schema is not None
        assert result.ui_schema.get("component") in ("smart_table", "document_preview")

        # Blackboard stores result
        stored = await bb.get_state("w_qt", "last_quotation_result")
        assert stored is not None


class TestWriterContract:
    @pytest.mark.asyncio
    async def test_contract(self, mock_redis):
        node = WriterNode()
        bb = TenantBlackboard(mock_redis, "smoke")

        result = await node.execute("w_ct", "smoke", {
            "skill": "contract",
            "data": {
                "party_a": "Shenzhen Tech Co., Ltd.",
                "party_b": "CSCEC 4th Bureau 3rd Engineering Co.",
                "items": [
                    {"name": "ThinkPad X1 Carbon", "spec": "i7/16G/512G", "qty": 5, "unit_price": 9999},
                ],
                "total_amount": 49995,
                "delivery_address": "Nanshan District, Shenzhen",
                "payment_terms": "30 days after acceptance",
            },
        }, bb)

        print(f"\n--- Contract: {result.status} ---")
        print(json.dumps(result.result, ensure_ascii=False, indent=2)[:600])

        assert result.is_success, f"Failed: {result.error_message}"
        assert result.result.get("type") in ("document", "document_fill")
        assert result.ui_schema.get("component") == "document_preview"
        # Contract UI should have "generate delivery note" action
        actions_str = json.dumps(result.ui_schema.get("actions", []), ensure_ascii=False)
        print(f"Actions: {actions_str}")

        stored = await bb.get_state("w_ct", "last_contract_result")
        assert stored is not None


class TestWriterDeliveryNote:
    @pytest.mark.asyncio
    async def test_delivery_note(self, mock_redis):
        node = WriterNode()
        bb = TenantBlackboard(mock_redis, "smoke")

        result = await node.execute("w_dn", "smoke", {
            "skill": "delivery_note",
            "data": {
                "contract_no": "HT-20260215-001",
                "sender": "Shenzhen Tech Co., Ltd.",
                "receiver": "CSCEC 4th Bureau",
                "address": "Nanshan District, Shenzhen",
                "contact": "Zhang San",
                "phone": "13800138000",
                "items": [
                    {"product": "ThinkPad X1 Carbon", "spec": "i7/16G/512G", "qty": 5, "unit": "units"},
                ],
            },
        }, bb)

        print(f"\n--- Delivery Note: {result.status} ---")
        print(json.dumps(result.result, ensure_ascii=False, indent=2)[:600])

        assert result.is_success, f"Failed: {result.error_message}"
        assert result.result.get("type") in ("document", "document_fill", "table")
        assert result.ui_schema is not None

        stored = await bb.get_state("w_dn", "last_delivery_note_result")
        assert stored is not None


class TestWriterFinancialReport:
    @pytest.mark.asyncio
    async def test_financial_report(self, mock_redis):
        node = WriterNode()
        bb = TenantBlackboard(mock_redis, "smoke")

        result = await node.execute("w_fr", "smoke", {
            "skill": "financial_report",
            "data": {
                "report_type": "monthly_report",
                "period": "2026-01",
                "contracts": [
                    {"contract_no": "HT-001", "amount": 49995, "paid": 49995, "invoice": "issued"},
                    {"contract_no": "HT-002", "amount": 120000, "paid": 80000, "invoice": "pending"},
                    {"contract_no": "HT-003", "amount": 35000, "paid": 0, "invoice": "not_issued"},
                ],
                "total_procurement": 204995,
                "total_paid": 129995,
                "total_unpaid": 75000,
            },
        }, bb)

        print(f"\n--- Financial Report: {result.status} ---")
        print(json.dumps(result.result, ensure_ascii=False, indent=2)[:800])

        assert result.is_success, f"Failed: {result.error_message}"
        assert result.result.get("type") in ("report", "table", "document")
        assert result.ui_schema is not None

        stored = await bb.get_state("w_fr", "last_financial_report_result")
        assert stored is not None


class TestWriterComparison:
    @pytest.mark.asyncio
    async def test_comparison_with_search_context(self, mock_redis):
        """Writer reads search result from Blackboard to generate comparison."""
        node = WriterNode()
        bb = TenantBlackboard(mock_redis, "smoke")

        await bb.set_state("w_cmp", "last_search_result", {
            "type": "table",
            "title": "A4 Paper Price Comparison",
            "columns": [
                {"key": "brand", "label": "Brand"},
                {"key": "spec", "label": "Spec"},
                {"key": "price", "label": "Price"},
                {"key": "rating", "label": "Rating"},
            ],
            "rows": [
                {"brand": "Deli", "spec": "A4 70g 500sheets", "price": "25.9", "rating": "4.8"},
                {"brand": "Comix", "spec": "A4 70g 500sheets", "price": "22.5", "rating": "4.6"},
                {"brand": "Tianzhang", "spec": "A4 80g 500sheets", "price": "28.0", "rating": "4.9"},
            ],
        })

        result = await node.execute("w_cmp", "smoke", {"skill": "comparison"}, bb)

        print(f"\n--- Comparison: {result.status} ---")
        print(json.dumps(result.result, ensure_ascii=False, indent=2)[:600])

        assert result.is_success, f"Failed: {result.error_message}"
        assert result.result.get("type") in ("table", "document")


class TestWriterGeneral:
    @pytest.mark.asyncio
    async def test_general_skill(self, mock_redis):
        node = WriterNode()
        bb = TenantBlackboard(mock_redis, "smoke")

        result = await node.execute("w_gen", "smoke", {
            "skill": "general",
            "data": {
                "task": "Write a supplier evaluation summary",
                "supplier": "JD Industrial",
                "categories": ["electronics", "office supplies"],
                "performance": {"on_time_rate": "95%", "defect_rate": "0.5%", "response_time": "2h"},
            },
        }, bb)

        print(f"\n--- General: {result.status} ---")
        assert result.is_success, f"Failed: {result.error_message}"

    @pytest.mark.asyncio
    async def test_no_data_returns_need_input(self, mock_redis):
        node = WriterNode()
        bb = TenantBlackboard(mock_redis, "smoke")

        result = await node.execute("w_empty", "smoke", {"skill": "contract"}, bb)
        assert result.status == "need_user_input"
