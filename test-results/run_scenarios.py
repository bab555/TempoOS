"""
TempoOS E2E Scenario Test Harness

Executes chained multi-turn business scenarios against the live TempoOS API,
validates SSE event streams, checks Redis Blackboard state between turns,
and generates a structured quality report.

Usage:
    cd /home/administrator/projects/数字员工统一平台
    source venv/bin/activate
    python test-results/run_scenarios.py
"""

import asyncio
import json
import time
import uuid
import sys
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx
import redis.asyncio as aioredis

API_BASE = "http://127.0.0.1:8200"
TENANT_ID = "default"
USER_ID = f"e2e-tester-{uuid.uuid4().hex[:8]}"
REDIS_URL = "redis://127.0.0.1:6379/1"

REPORT_PATH = os.path.join(os.path.dirname(__file__), "e2e_report.md")


@dataclass
class SSEEvent:
    event: str
    data: Dict[str, Any]
    raw: str = ""


@dataclass
class TurnResult:
    turn_num: int
    user_message: str
    session_id: Optional[str] = None
    events: List[SSEEvent] = field(default_factory=list)
    latency_ms: float = 0
    errors: List[str] = field(default_factory=list)
    assistant_text: str = ""
    ui_renders: List[Dict] = field(default_factory=list)
    tool_calls: List[Dict] = field(default_factory=list)
    scene: str = ""

    @property
    def event_types(self) -> List[str]:
        return [e.event for e in self.events]

    @property
    def has_ui_render(self) -> bool:
        return len(self.ui_renders) > 0


@dataclass
class ScenarioResult:
    name: str
    description: str
    turns: List[TurnResult] = field(default_factory=list)
    redis_checks: List[Dict] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    passed: bool = True


async def parse_sse_stream(response: httpx.Response) -> List[SSEEvent]:
    events = []
    current_event = ""
    async for line in response.aiter_lines():
        if line.startswith("event: "):
            current_event = line[7:]
        elif line.startswith("data: ") and current_event:
            raw = line[6:]
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"raw": raw}
            events.append(SSEEvent(event=current_event, data=data, raw=raw))
            current_event = ""
    return events


async def send_turn(
    client: httpx.AsyncClient,
    session_id: Optional[str],
    message: str,
    turn_num: int,
    files: Optional[List[Dict]] = None,
) -> TurnResult:
    result = TurnResult(turn_num=turn_num, user_message=message)
    body: Dict[str, Any] = {
        "session_id": session_id,
        "messages": [{"role": "user", "content": message}],
    }
    if files:
        body["messages"][0]["files"] = files

    start = time.time()
    try:
        async with client.stream(
            "POST",
            f"{API_BASE}/api/agent/chat",
            json=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "X-Tenant-Id": TENANT_ID,
                "X-User-Id": USER_ID,
            },
            timeout=180.0,
        ) as resp:
            result.events = await parse_sse_stream(resp)
    except Exception as e:
        result.errors.append(f"HTTP error: {e}")
        result.latency_ms = (time.time() - start) * 1000
        return result

    result.latency_ms = (time.time() - start) * 1000

    msg_buffers: Dict[str, str] = {}
    for ev in result.events:
        if ev.event == "session_init":
            result.session_id = ev.data.get("session_id")
        elif ev.event == "thinking":
            if "scene" in ev.data:
                result.scene = ev.data["scene"]
        elif ev.event == "message":
            mid = ev.data.get("message_id", "")
            content = ev.data.get("content", "")
            if ev.data.get("mode") == "delta" and mid:
                msg_buffers[mid] = msg_buffers.get(mid, "") + content
            elif ev.data.get("mode") == "full":
                msg_buffers[mid or "full"] = content
        elif ev.event == "ui_render":
            result.ui_renders.append(ev.data)
        elif ev.event == "tool_start":
            result.tool_calls.append(ev.data)
        elif ev.event == "error":
            result.errors.append(ev.data.get("message", str(ev.data)))

    result.assistant_text = "\n".join(msg_buffers.values())
    return result


async def check_redis_session(redis: aioredis.Redis, session_id: str) -> Dict[str, Any]:
    """Inspect Redis state for a session to validate Blackboard persistence."""
    info: Dict[str, Any] = {}
    session_key = f"tempo:{TENANT_ID}:session:{session_id}"
    all_fields = await redis.hgetall(session_key)
    info["session_fields"] = {k: _try_json(v) for k, v in all_fields.items()}
    info["session_ttl"] = await redis.ttl(session_key)

    chat_key = f"tempo:{TENANT_ID}:chat:{session_id}"
    chat_len = await redis.llen(chat_key)
    info["chat_history_length"] = chat_len
    info["chat_ttl"] = await redis.ttl(chat_key)

    for tool in ("search", "data_query"):
        rk = f"tempo:{TENANT_ID}:session:{session_id}:results:{tool}"
        rlen = await redis.llen(rk)
        if rlen > 0:
            info[f"accumulated_{tool}_results"] = rlen

    art_key = f"tempo:{TENANT_ID}:session:{session_id}:artifacts"
    artifacts = await redis.smembers(art_key)
    if artifacts:
        info["artifact_ids"] = list(artifacts)

    return info


def _try_json(val):
    if isinstance(val, bytes):
        val = val.decode("utf-8", errors="replace")
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return val


def print_turn(tr: TurnResult):
    status = "PASS" if not tr.errors else "FAIL"
    print(f"\n  Turn {tr.turn_num} [{status}] ({tr.latency_ms:.0f}ms)")
    print(f"    User: {tr.user_message[:80]}...")
    print(f"    Scene: {tr.scene or 'N/A'}")
    print(f"    Events: {' -> '.join(dict.fromkeys(tr.event_types))}")
    print(f"    Tools: {[t.get('tool', t.get('title', '?')) for t in tr.tool_calls] or 'none'}")
    print(f"    UI Renders: {[u.get('component', '?') for u in tr.ui_renders] or 'none'}")
    print(f"    Assistant: {tr.assistant_text[:120]}{'...' if len(tr.assistant_text) > 120 else ''}")
    if tr.errors:
        for e in tr.errors:
            print(f"    ERROR: {e}")


# ═══════════════════════════════════════════════════════════════
# SCENARIO A: Full Procurement Chain
# search → quotation → contract → delivery_note (4 turns, 1 session)
# ═══════════════════════════════════════════════════════════════

async def scenario_a(client: httpx.AsyncClient, redis: aioredis.Redis) -> ScenarioResult:
    sc = ScenarioResult(
        name="Scenario A: Full Procurement Chain",
        description="search → quotation → contract → delivery_note across 4 turns with shared session & Blackboard context",
    )
    session_id = None

    # Turn 1: Search for products
    print("\n  [Turn 1] Searching for products...")
    t1 = await send_turn(client, session_id, (
        "帮我在网上搜索3款办公笔记本电脑，要求i7处理器、16GB内存、512GB SSD，"
        "对比不同品牌（联想ThinkPad、戴尔Latitude、惠普EliteBook）的价格和配置，"
        "做成比价表"
    ), 1)
    print_turn(t1)
    session_id = t1.session_id
    sc.turns.append(t1)

    if not session_id:
        sc.issues.append("Turn 1 failed to return session_id")
        sc.passed = False
        return sc

    # Check Redis after Turn 1
    r1 = await check_redis_session(redis, session_id)
    sc.redis_checks.append({"after_turn": 1, **r1})
    print(f"    Redis: chat_len={r1['chat_history_length']}, fields={list(r1['session_fields'].keys())[:5]}")

    # Turn 2: Generate quotation from search results
    print("\n  [Turn 2] Generating quotation from search results...")
    t2 = await send_turn(client, session_id, (
        "根据刚才的搜索比价结果，帮我生成一份正式的报价表。"
        "客户是中建四局第三工程公司，采购数量50台，联系人张经理。"
    ), 2)
    print_turn(t2)
    sc.turns.append(t2)

    r2 = await check_redis_session(redis, session_id)
    sc.redis_checks.append({"after_turn": 2, **r2})
    has_search_ctx = "last_search_result" in r2["session_fields"] or r2.get("accumulated_search_results", 0) > 0
    print(f"    Redis: chat_len={r2['chat_history_length']}, search_ctx_exists={has_search_ctx}")
    if not has_search_ctx:
        sc.issues.append("Turn 2: Blackboard missing search context from Turn 1 (search may not have been invoked)")

    # Turn 3: Generate contract from quotation
    print("\n  [Turn 3] Generating contract from quotation...")
    t3 = await send_turn(client, session_id, (
        "很好，现在根据这份报价表生成一份采购合同。"
        "甲方：中建四局第三工程公司，地址：广州市天河区XX路XX号。"
        "乙方：联想授权经销商XX科技有限公司，地址：深圳市南山区XX路XX号。"
        "交货期限：2026年4月15日前，付款方式：货到验收合格后30日内银行转账。"
    ), 3)
    print_turn(t3)
    sc.turns.append(t3)

    r3 = await check_redis_session(redis, session_id)
    sc.redis_checks.append({"after_turn": 3, **r3})
    print(f"    Redis: chat_len={r3['chat_history_length']}, artifacts={r3.get('artifact_ids', [])[:3]}")

    # Turn 4: Generate delivery note from contract
    print("\n  [Turn 4] Generating delivery note from contract...")
    t4 = await send_turn(client, session_id, (
        "最后，根据这份采购合同生成一份送货单。"
        "送货日期：2026年4月10日，收货地址：广州市天河区XX路XX号中建四局仓库，"
        "收货人：李工，联系电话：13800138000。"
    ), 4)
    print_turn(t4)
    sc.turns.append(t4)

    r4 = await check_redis_session(redis, session_id)
    sc.redis_checks.append({"after_turn": 4, **r4})
    print(f"    Redis: chat_len={r4['chat_history_length']}, total_artifacts={len(r4.get('artifact_ids', []))}")

    # Validate chain integrity
    if r4["chat_history_length"] < 8:
        sc.issues.append(f"Chat history too short ({r4['chat_history_length']}), expected >= 8 for 4 turns")

    for i, t in enumerate(sc.turns):
        if t.errors:
            sc.issues.append(f"Turn {i+1} had errors: {t.errors}")
            sc.passed = False

    return sc


# ═══════════════════════════════════════════════════════════════
# SCENARIO B: Financial Report Chain
# provide data → financial report → follow-up drill-down (3 turns)
# ═══════════════════════════════════════════════════════════════

async def scenario_b(client: httpx.AsyncClient, redis: aioredis.Redis) -> ScenarioResult:
    sc = ScenarioResult(
        name="Scenario B: Financial Report Chain",
        description="data input → financial report → follow-up analysis across 3 turns with accumulated context",
    )
    session_id = None

    # Turn 1: Provide financial data and request report
    print("\n  [Turn 1] Providing financial data and requesting report...")
    t1 = await send_turn(client, session_id, (
        "帮我生成2026年第一季度的采购对账报表。以下是合同数据：\n"
        "1. 合同编号 HT-2026-001，供应商：联想科技，合同金额50万元，已付款30万元，已开发票35万元\n"
        "2. 合同编号 HT-2026-002，供应商：戴尔中国，合同金额75万元，已付款75万元，已开发票75万元\n"
        "3. 合同编号 HT-2026-003，供应商：华为终端，合同金额32万元，已付款0元，已开发票0元\n"
        "4. 合同编号 HT-2026-004，供应商：惠普中国，合同金额18万元，已付款18万元，已开发票12万元\n"
        "请生成包含关键指标、趋势图表和对账明细的完整报表。"
    ), 1)
    print_turn(t1)
    session_id = t1.session_id
    sc.turns.append(t1)

    if not session_id:
        sc.issues.append("Turn 1 failed to return session_id")
        sc.passed = False
        return sc

    r1 = await check_redis_session(redis, session_id)
    sc.redis_checks.append({"after_turn": 1, **r1})
    print(f"    Redis: chat_len={r1['chat_history_length']}, fields={list(r1['session_fields'].keys())[:5]}")

    # Turn 2: Drill down on anomalies
    print("\n  [Turn 2] Drilling down on anomalies...")
    t2 = await send_turn(client, session_id, (
        "我注意到HT-2026-001的发票金额(35万)大于已付款金额(30万)，"
        "而HT-2026-003完全没有付款和开票。请帮我分析这些异常情况，"
        "并给出处理建议。同时把异常合同单独列一个风险提示表。"
    ), 2)
    print_turn(t2)
    sc.turns.append(t2)

    r2 = await check_redis_session(redis, session_id)
    sc.redis_checks.append({"after_turn": 2, **r2})
    print(f"    Redis: chat_len={r2['chat_history_length']}")

    # Turn 3: Request export-ready summary
    print("\n  [Turn 3] Requesting export-ready summary...")
    t3 = await send_turn(client, session_id, (
        "好的，请把刚才的对账报表和异常分析合并，生成一份可以提交给财务总监的季度采购总结报告。"
        "要包含：1) 总体概况 2) 各供应商明细 3) 异常事项及处理建议 4) 下季度预算建议"
    ), 3)
    print_turn(t3)
    sc.turns.append(t3)

    r3 = await check_redis_session(redis, session_id)
    sc.redis_checks.append({"after_turn": 3, **r3})
    print(f"    Redis: chat_len={r3['chat_history_length']}, artifacts={len(r3.get('artifact_ids', []))}")

    for i, t in enumerate(sc.turns):
        if t.errors:
            sc.issues.append(f"Turn {i+1} had errors: {t.errors}")
            sc.passed = False

    return sc


# ═══════════════════════════════════════════════════════════════
# SCENARIO C: Research + Long Document
# web search → proposal (outline → chapter-by-chapter)
# ═══════════════════════════════════════════════════════════════

async def scenario_c(client: httpx.AsyncClient, redis: aioredis.Redis) -> ScenarioResult:
    sc = ScenarioResult(
        name="Scenario C: Research + Long Document",
        description="web search → long-form proposal with outline and chapter-by-chapter generation",
    )
    session_id = None

    # Turn 1: Research phase
    print("\n  [Turn 1] Searching for industry research...")
    t1 = await send_turn(client, session_id, (
        "帮我搜索2025-2026年国内建筑行业数字化采购的最新趋势、典型案例和市场规模数据。"
        "重点关注：1) 建筑央企的数字化采购实践 2) AI在采购领域的应用 3) 供应链数字化平台的市场格局"
    ), 1)
    print_turn(t1)
    session_id = t1.session_id
    sc.turns.append(t1)

    if not session_id:
        sc.issues.append("Turn 1 failed to return session_id")
        sc.passed = False
        return sc

    r1 = await check_redis_session(redis, session_id)
    sc.redis_checks.append({"after_turn": 1, **r1})
    print(f"    Redis: chat_len={r1['chat_history_length']}, search_results={r1.get('accumulated_search_results', 0)}")

    # Turn 2: Generate long-form proposal using search results
    print("\n  [Turn 2] Generating long-form proposal (this will take a while)...")
    t2 = await send_turn(client, session_id, (
        "根据刚才的搜索结果，帮我撰写一份企划书：《中建四局数字化采购平台建设方案》。\n"
        "目标读者：集团管理层和信息化部门领导。\n"
        "核心要点：\n"
        "1. 行业背景与趋势分析（引用搜索到的数据和案例）\n"
        "2. 当前采购流程痛点分析\n"
        "3. 数字化采购平台解决方案（包含AI数字员工的定位）\n"
        "4. 实施计划与里程碑（分三期）\n"
        "5. 投资预算与预期效益（ROI分析）\n"
        "篇幅要求：每章节1000-1500字，总计约6000字。"
    ), 2)
    print_turn(t2)
    sc.turns.append(t2)

    r2 = await check_redis_session(redis, session_id)
    sc.redis_checks.append({"after_turn": 2, **r2})
    has_search_in_ctx = "last_search_result" in r2["session_fields"]
    print(f"    Redis: chat_len={r2['chat_history_length']}, search_ctx_carried={has_search_in_ctx}, artifacts={len(r2.get('artifact_ids', []))}")

    if not has_search_in_ctx and r2.get("accumulated_search_results", 0) == 0:
        sc.issues.append("Turn 2: Search context from Turn 1 not found in Blackboard — writer may not have used search results")

    # Validate long-form output
    if t2.ui_renders:
        for ui in t2.ui_renders:
            sections = ui.get("data", {}).get("sections", [])
            if sections and len(sections) >= 3:
                print(f"    Long-form: {len(sections)} sections generated")
            outline = ui.get("data", {}).get("outline", [])
            if outline:
                print(f"    Outline: {len(outline)} chapters")

    for i, t in enumerate(sc.turns):
        if t.errors:
            sc.issues.append(f"Turn {i+1} had errors: {t.errors}")
            sc.passed = False

    return sc


# ═══════════════════════════════════════════════════════════════
# SCENARIO D: OSS Signing + File Reference + Template Contract
# oss sign → chat with file ref → contract generation
# ═══════════════════════════════════════════════════════════════

async def scenario_d(client: httpx.AsyncClient, redis: aioredis.Redis) -> ScenarioResult:
    sc = ScenarioResult(
        name="Scenario D: File Upload Flow + Template Contract",
        description="OSS post-signature → chat with file attachment → template-based contract generation",
    )

    # Step 1: Test OSS post-signature endpoint
    print("\n  [Step 1] Testing OSS post-signature endpoint...")
    try:
        resp = await client.post(
            f"{API_BASE}/api/oss/post-signature",
            json={
                "filename": "采购需求清单.xlsx",
                "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "dir": "templates/",
                "expire_seconds": 600,
            },
            headers={
                "X-Tenant-Id": TENANT_ID,
                "X-User-Id": USER_ID,
            },
        )
        oss_data = resp.json()
        oss_ok = resp.status_code == 200 and "upload" in oss_data and "object" in oss_data
        print(f"    OSS Signature: {'PASS' if oss_ok else 'FAIL'} (status={resp.status_code})")
        if oss_ok:
            print(f"    Upload URL: {oss_data['upload']['url']}")
            print(f"    Object Key: {oss_data['object']['key'][:60]}...")
            oss_url = oss_data["object"]["url"]
        else:
            sc.issues.append(f"OSS signature failed: {resp.text[:200]}")
            oss_url = "https://example-oss.com/test/fake_file.xlsx"
    except Exception as e:
        sc.issues.append(f"OSS signature error: {e}")
        oss_url = "https://example-oss.com/test/fake_file.xlsx"
        print(f"    OSS Signature: ERROR ({e})")

    # Step 2: Send chat with file reference (simulating uploaded file)
    session_id = None
    print("\n  [Turn 1] Sending chat with file reference...")
    t1 = await send_turn(client, session_id, (
        "我上传了一份采购需求清单，请根据清单内容帮我生成采购合同。"
        "甲方：中建四局装饰工程有限公司，乙方从清单中的推荐供应商选取。"
        "清单内容概要：50台ThinkPad X1 Carbon（单价9299元），30台Dell U2723QE显示器（单价3299元），"
        "200套罗技MK470键鼠套装（单价299元）。总预算约60万元。"
    ), 1, files=[{
        "name": "采购需求清单.xlsx",
        "url": oss_url,
        "type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }])
    print_turn(t1)
    session_id = t1.session_id
    sc.turns.append(t1)

    if not session_id:
        sc.issues.append("Turn 1 failed to return session_id")
        sc.passed = False
        return sc

    r1 = await check_redis_session(redis, session_id)
    sc.redis_checks.append({"after_turn": 1, **r1})
    has_file_state = any("file" in k.lower() for k in r1["session_fields"].keys())
    print(f"    Redis: chat_len={r1['chat_history_length']}, file_state_tracked={has_file_state}")

    # Step 3: Follow-up — generate delivery note from contract
    print("\n  [Turn 2] Generating delivery note from contract context...")
    t2 = await send_turn(client, session_id, (
        "合同看起来不错。现在请根据合同内容生成送货单，分两批送货：\n"
        "第一批（2026年4月10日）：50台笔记本电脑\n"
        "第二批（2026年4月20日）：30台显示器 + 200套键鼠\n"
        "收货地址：广州市天河区XX路XX号，收货人：王工，电话：13900139000"
    ), 2)
    print_turn(t2)
    sc.turns.append(t2)

    r2 = await check_redis_session(redis, session_id)
    sc.redis_checks.append({"after_turn": 2, **r2})
    print(f"    Redis: chat_len={r2['chat_history_length']}, artifacts={len(r2.get('artifact_ids', []))}")

    for i, t in enumerate(sc.turns):
        if t.errors:
            sc.issues.append(f"Turn {i+1} had errors: {t.errors}")
            sc.passed = False

    return sc


# ═══════════════════════════════════════════════════════════════
# Report Generator
# ═══════════════════════════════════════════════════════════════

def generate_report(scenarios: List[ScenarioResult]) -> str:
    lines = [
        "# TempoOS E2E Test Report",
        f"\n**Date**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Tenant**: {TENANT_ID} | **User**: {USER_ID}",
        f"**API**: {API_BASE}",
        "",
        "---",
        "",
        "## Summary",
        "",
        "| Scenario | Turns | Passed | Issues | Total Latency |",
        "|----------|-------|--------|--------|---------------|",
    ]

    total_issues = 0
    for sc in scenarios:
        total_lat = sum(t.latency_ms for t in sc.turns)
        n_issues = len(sc.issues)
        total_issues += n_issues
        lines.append(
            f"| {sc.name} | {len(sc.turns)} | "
            f"{'PASS' if sc.passed else 'FAIL'} | {n_issues} | {total_lat:.0f}ms |"
        )

    lines.append("")
    lines.append(f"**Total Issues: {total_issues}**")
    lines.append("")

    for sc in scenarios:
        lines.append(f"---\n\n## {sc.name}\n")
        lines.append(f"*{sc.description}*\n")

        for t in sc.turns:
            lines.append(f"### Turn {t.turn_num}")
            lines.append(f"- **User**: {t.user_message[:120]}{'...' if len(t.user_message) > 120 else ''}")
            lines.append(f"- **Session**: `{t.session_id}`")
            lines.append(f"- **Scene**: {t.scene or 'N/A'}")
            lines.append(f"- **Latency**: {t.latency_ms:.0f}ms")
            lines.append(f"- **Event Flow**: `{' → '.join(dict.fromkeys(t.event_types))}`")
            lines.append(f"- **Tools Called**: {[tc.get('tool', '?') for tc in t.tool_calls] or 'none'}")

            if t.ui_renders:
                for ui in t.ui_renders:
                    comp = ui.get("component", "?")
                    title = ui.get("title", "?")
                    has_data = bool(ui.get("data"))
                    has_actions = bool(ui.get("actions"))
                    lines.append(f"- **UI Render**: `{comp}` — \"{title}\" (data={'yes' if has_data else 'NO'}, actions={'yes' if has_actions else 'no'})")

                    if comp == "smart_table":
                        cols = ui.get("data", {}).get("columns", [])
                        rows = ui.get("data", {}).get("rows", [])
                        lines.append(f"  - Columns: {len(cols)}, Rows: {len(rows)}")
                    elif comp == "document_preview":
                        secs = ui.get("data", {}).get("sections", [])
                        fields = ui.get("data", {}).get("fields", {})
                        lines.append(f"  - Sections: {len(secs)}, Fields: {len(fields)}")
                    elif comp == "chart_report":
                        metrics = ui.get("data", {}).get("metrics", [])
                        charts = ui.get("data", {}).get("charts", [])
                        lines.append(f"  - Metrics: {len(metrics)}, Charts: {len(charts)}")

            lines.append(f"- **Assistant Text** (first 300 chars):")
            lines.append(f"  > {t.assistant_text[:300]}{'...' if len(t.assistant_text) > 300 else ''}")

            if t.errors:
                lines.append(f"- **ERRORS**: {t.errors}")
            lines.append("")

        if sc.redis_checks:
            lines.append("### Redis / Blackboard State\n")
            for rc in sc.redis_checks:
                turn = rc.pop("after_turn", "?")
                lines.append(f"**After Turn {turn}:**")
                lines.append(f"- Chat history length: {rc.get('chat_history_length', '?')}")
                lines.append(f"- Session TTL: {rc.get('session_ttl', '?')}s")
                lines.append(f"- Chat TTL: {rc.get('chat_ttl', '?')}s")
                sf = rc.get("session_fields", {})
                if sf:
                    lines.append(f"- Session fields: `{list(sf.keys())}`")
                for tool in ("search", "data_query"):
                    k = f"accumulated_{tool}_results"
                    if k in rc:
                        lines.append(f"- Accumulated {tool} results: {rc[k]}")
                arts = rc.get("artifact_ids", [])
                if arts:
                    lines.append(f"- Artifacts: {arts[:5]}{'...' if len(arts) > 5 else ''}")
                lines.append("")

        if sc.issues:
            lines.append("### Issues Found\n")
            for issue in sc.issues:
                lines.append(f"- {issue}")
            lines.append("")

    # API Compliance section
    lines.append("---\n\n## API Compliance Assessment\n")
    all_events_seen = set()
    for sc in scenarios:
        for t in sc.turns:
            all_events_seen.update(t.event_types)

    expected = {"session_init", "thinking", "message", "done"}
    optional = {"tool_start", "tool_done", "ui_render", "error", "ping"}
    lines.append(f"- **Events observed**: `{sorted(all_events_seen)}`")
    lines.append(f"- **Required events present**: {expected.issubset(all_events_seen)}")
    lines.append(f"- **Optional events seen**: `{sorted(all_events_seen & optional)}`")

    has_delta = False
    has_seq = False
    has_message_id = False
    for sc in scenarios:
        for t in sc.turns:
            for ev in t.events:
                if ev.event == "message":
                    if ev.data.get("mode") == "delta":
                        has_delta = True
                    if "seq" in ev.data:
                        has_seq = True
                    if "message_id" in ev.data:
                        has_message_id = True

    lines.append(f"- **Message delta streaming**: {'YES' if has_delta else 'NO'}")
    lines.append(f"- **Message seq numbering**: {'YES' if has_seq else 'NO'}")
    lines.append(f"- **Message ID tracking**: {'YES' if has_message_id else 'NO'}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

async def main():
    print("=" * 60)
    print("  TempoOS E2E Scenario Tests")
    print("=" * 60)

    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await redis.ping()
        print(f"\nRedis: connected ({REDIS_URL})")
    except Exception as e:
        print(f"\nRedis connection failed: {e}")
        sys.exit(1)

    async with httpx.AsyncClient() as client:
        scenarios: List[ScenarioResult] = []

        print("\n" + "=" * 60)
        print("  SCENARIO A: Full Procurement Chain")
        print("=" * 60)
        sa = await scenario_a(client, redis)
        scenarios.append(sa)

        print("\n" + "=" * 60)
        print("  SCENARIO B: Financial Report Chain")
        print("=" * 60)
        sb = await scenario_b(client, redis)
        scenarios.append(sb)

        print("\n" + "=" * 60)
        print("  SCENARIO C: Research + Long Document")
        print("=" * 60)
        sc_result = await scenario_c(client, redis)
        scenarios.append(sc_result)

        print("\n" + "=" * 60)
        print("  SCENARIO D: File Upload Flow + Template Contract")
        print("=" * 60)
        sd = await scenario_d(client, redis)
        scenarios.append(sd)

    report = generate_report(scenarios)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n{'=' * 60}")
    print(f"  Report written to: {REPORT_PATH}")
    print(f"{'=' * 60}")

    await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
