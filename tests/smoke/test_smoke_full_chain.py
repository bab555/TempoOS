# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Smoke test -- Full business chain via real DashScope.

Simulates the complete procurement workflow:
  1. Search suppliers + price comparison
  2. Generate quotation (reads search result from Blackboard)
  3. Generate contract (reads quotation from Blackboard)
  4. Generate delivery note (reads contract from Blackboard)
  5. Generate financial report (reads all previous data from Blackboard)

Each step verifies:
  - Node executes successfully
  - Result is stored in Blackboard
  - Next step can read previous results from Blackboard

Run:  pytest tests/smoke/test_smoke_full_chain.py -v -s --timeout=300
"""

import json

import pytest

from tempo_os.core.config import settings
from tempo_os.memory.blackboard import TenantBlackboard
from tempo_os.nodes.search import SearchNode
from tempo_os.nodes.writer import WriterNode

pytestmark = pytest.mark.skipif(
    not settings.DASHSCOPE_API_KEY,
    reason="DASHSCOPE_API_KEY not configured",
)

SESSION = "chain_session"
TENANT = "chain_tenant"


class TestFullBusinessChain:
    """
    Sequential test: each step depends on the previous one.
    Uses a shared session_id so Blackboard data flows through.
    """

    @pytest.mark.asyncio
    async def test_full_chain(self, mock_redis):
        bb = TenantBlackboard(mock_redis, TENANT)
        search_node = SearchNode()
        writer_node = WriterNode()

        # ============================================================
        # Step 1: Search -- find office equipment suppliers
        # ============================================================
        print("\n" + "=" * 60)
        print("STEP 1: Search suppliers")
        print("=" * 60)

        search_result = await search_node.execute(SESSION, TENANT, {
            "query": "office laptop price comparison ThinkPad Dell HP 2026",
            "output_format": "table",
        }, bb)

        assert search_result.is_success, f"Search failed: {search_result.error_message}"
        print(f"  Status: {search_result.status}")
        print(f"  Type: {search_result.result.get('type')}")
        print(f"  UI: {search_result.ui_schema.get('component') if search_result.ui_schema else 'None'}")

        bb_search = await bb.get_state(SESSION, "last_search_result")
        assert bb_search is not None, "Search result not in Blackboard"
        print(f"  Blackboard last_search_result: OK (type={bb_search.get('type')})")

        # ============================================================
        # Step 2: Quotation -- generate based on search results
        # ============================================================
        print("\n" + "=" * 60)
        print("STEP 2: Generate Quotation")
        print("=" * 60)

        qt_result = await writer_node.execute(SESSION, TENANT, {
            "skill": "quotation",
            "data": {
                "project_name": "2026 Office IT Procurement",
                "client": "CSCEC 4th Bureau Project Department",
                "items": [
                    {"name": "ThinkPad X1 Carbon Gen12", "spec": "i7/16G/512G", "qty": 10, "unit_price": 9999},
                    {"name": "Dell P2723QE Monitor", "spec": "27in 4K", "qty": 10, "unit_price": 3500},
                    {"name": "HP LaserJet M404dn", "spec": "Black & White Laser", "qty": 3, "unit_price": 2800},
                ],
            },
        }, bb)

        assert qt_result.is_success, f"Quotation failed: {qt_result.error_message}"
        print(f"  Status: {qt_result.status}")
        print(f"  Type: {qt_result.result.get('type')}")
        print(f"  UI: {qt_result.ui_schema.get('component') if qt_result.ui_schema else 'None'}")

        bb_qt = await bb.get_state(SESSION, "last_quotation_result")
        assert bb_qt is not None, "Quotation not in Blackboard"
        print(f"  Blackboard last_quotation_result: OK")

        # ============================================================
        # Step 3: Contract -- generate based on quotation
        # ============================================================
        print("\n" + "=" * 60)
        print("STEP 3: Generate Contract")
        print("=" * 60)

        ct_result = await writer_node.execute(SESSION, TENANT, {
            "skill": "contract",
            "data": {
                "party_a": "Shenzhen Digital Tech Co., Ltd.",
                "party_b": "CSCEC 4th Bureau 3rd Engineering Co.",
                "items": [
                    {"name": "ThinkPad X1 Carbon Gen12", "spec": "i7/16G/512G", "qty": 10, "unit_price": 9999},
                    {"name": "Dell P2723QE Monitor", "spec": "27in 4K", "qty": 10, "unit_price": 3500},
                    {"name": "HP LaserJet M404dn", "spec": "Black & White Laser", "qty": 3, "unit_price": 2800},
                ],
                "total_amount": 143390,
                "delivery_address": "CSCEC Project Site, Shenzhen",
                "payment_terms": "50% advance, 50% upon acceptance within 30 days",
            },
        }, bb)

        assert ct_result.is_success, f"Contract failed: {ct_result.error_message}"
        print(f"  Status: {ct_result.status}")
        print(f"  Type: {ct_result.result.get('type')}")

        bb_ct = await bb.get_state(SESSION, "last_contract_result")
        assert bb_ct is not None, "Contract not in Blackboard"
        print(f"  Blackboard last_contract_result: OK")

        # ============================================================
        # Step 4: Delivery Note -- generate based on contract
        # ============================================================
        print("\n" + "=" * 60)
        print("STEP 4: Generate Delivery Note")
        print("=" * 60)

        dn_result = await writer_node.execute(SESSION, TENANT, {
            "skill": "delivery_note",
            "data": {
                "contract_no": ct_result.result.get("meta", {}).get("contract_no", "HT-20260215-001"),
                "sender": "Shenzhen Digital Tech Co., Ltd.",
                "receiver": "CSCEC 4th Bureau 3rd Engineering Co.",
                "address": "CSCEC Project Site, Shenzhen",
                "contact": "Li Wei",
                "phone": "13800138000",
                "items": [
                    {"product": "ThinkPad X1 Carbon Gen12", "spec": "i7/16G/512G", "qty": 10, "unit": "units"},
                    {"product": "Dell P2723QE Monitor", "spec": "27in 4K", "qty": 10, "unit": "units"},
                    {"product": "HP LaserJet M404dn", "spec": "B&W Laser", "qty": 3, "unit": "units"},
                ],
            },
        }, bb)

        assert dn_result.is_success, f"Delivery note failed: {dn_result.error_message}"
        print(f"  Status: {dn_result.status}")
        print(f"  Type: {dn_result.result.get('type')}")

        bb_dn = await bb.get_state(SESSION, "last_delivery_note_result")
        assert bb_dn is not None, "Delivery note not in Blackboard"
        print(f"  Blackboard last_delivery_note_result: OK")

        # ============================================================
        # Step 5: Financial Report -- aggregate all previous data
        # ============================================================
        print("\n" + "=" * 60)
        print("STEP 5: Generate Financial Report")
        print("=" * 60)

        fr_result = await writer_node.execute(SESSION, TENANT, {
            "skill": "financial_report",
            "data": {
                "report_type": "monthly_report",
                "period": "2026-02",
                "contracts": [
                    {
                        "contract_no": ct_result.result.get("meta", {}).get("contract_no", "HT-001"),
                        "amount": 143390,
                        "paid": 71695,
                        "unpaid": 71695,
                        "invoice_status": "partially_issued",
                    },
                ],
                "total_procurement": 143390,
                "total_paid": 71695,
                "total_unpaid": 71695,
            },
        }, bb)

        assert fr_result.is_success, f"Financial report failed: {fr_result.error_message}"
        print(f"  Status: {fr_result.status}")
        print(f"  Type: {fr_result.result.get('type')}")

        bb_fr = await bb.get_state(SESSION, "last_financial_report_result")
        assert bb_fr is not None, "Financial report not in Blackboard"
        print(f"  Blackboard last_financial_report_result: OK")

        # ============================================================
        # Final: Verify all Blackboard keys exist for this session
        # ============================================================
        print("\n" + "=" * 60)
        print("FINAL: Blackboard State Verification")
        print("=" * 60)

        all_state = await bb.get_state(SESSION)
        expected_keys = [
            "last_search_query",
            "last_search_result",
            "last_quotation_result",
            "last_contract_result",
            "last_delivery_note_result",
            "last_financial_report_result",
        ]
        for key in expected_keys:
            assert key in all_state, f"Missing Blackboard key: {key}"
            print(f"  {key}: OK")

        print(f"\n  Total Blackboard keys for session: {len(all_state)}")

        # ============================================================
        # Verify: Accumulated search results preserved
        # ============================================================
        print("\n" + "=" * 60)
        print("VERIFY: Accumulated Results")
        print("=" * 60)

        search_accumulated = await bb.get_results(SESSION, "search")
        print(f"  Accumulated search results: {len(search_accumulated)}")
        assert len(search_accumulated) >= 1, "No accumulated search results"

        # ============================================================
        # Verify: Session TTL is set
        # ============================================================
        from tempo_os.kernel.namespace import get_key
        session_key = get_key(TENANT, "session", SESSION)
        ttl = await mock_redis.ttl(session_key)
        print(f"  Session key TTL: {ttl}s")
        assert ttl > 0, "Session key has no TTL (P0 fix not applied)"

        print("\n*** FULL CHAIN PASSED ***")
