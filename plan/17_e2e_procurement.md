# Plan 17：端到端交互验收 — 采购全流程（不做前端渲染）
> **Task ID**: `17_e2e_procurement`
> **目标**: 完整跑通"搜充电宝→选供应商→报价→合同→记账"，验证平台+节点+铜炉的**交互能力**：HTTP API 可推进、WS 可订阅、事件中包含可校验的 `ui_schema`
> **依赖**: `13_supply_chain_nodes`, `16_a2ui_protocol`
> **预估**: 2 天

---

## 验证场景

### 场景：采购充电宝

```
用户操作                          平台行为                               交互验收（不渲染UI）
─────────────────────────────────────────────────────────────────────────────────
1. 点击"开始采购"                → 启动 procurement_flow               → WS 收到 progress/ui_schema 事件
   输入"充电宝 20个"             → 参数传入 sourcing 节点

2. sourcing 执行完成             → STEP_DONE → FSM 进入 sourcing_done  → WS 事件 payload 含 ui_schema（表格/选择列表）
                                 → 铜炉自动持久化 sourcing_result       → ui_schema 通过 Plan16 合约校验

3. 用户选择供应商                → USER_CONFIRM → FSM 进入 quoting     → 通过 HTTP 推进事件成功
   quoting 从 Blackboard 读      → STEP_DONE → FSM 进入 quoting_done   → WS 收到报价 ui_schema（表格+action_buttons）
   sourcing_result

4. 用户确认报价                  → USER_CONFIRM → FSM 进入 contracting → WS 收到文书 ui_schema（markdown+file_preview）
   doc_writer 从 Blackboard 读    → STEP_DONE → FSM 进入 contract_done  → ui_schema 合约校验通过
   quotation 数据                                                       + 下载按钮

5. 用户确认合同                  → USER_CONFIRM → FSM 进入 finance     → WS 收到报表 ui_schema（kpi_card/table/chart 任意组合）
   finance 从 Blackboard 读       → STEP_DONE → FSM 进入 end           → ui_schema 合约校验通过
   合同金额                                                             + 折线图

6. 流程结束                      → Session completed                   → WS 收到 completed 状态事件
                                 → 铜炉已持久化全部产物
```

### 验证检查清单

**工作流引擎**:
- [ ] Session 从 idle → running → waiting → running → ... → completed
- [ ] FSM 状态推进正确，无遗漏/重复
- [ ] PG workflow_events 记录完整（可回放）

**节点执行**:
- [ ] 4 个节点依次执行成功
- [ ] 每个节点返回有效 ui_schema
- [ ] 数据通过 Blackboard artifact 正确流转

**数据服务（铜炉）**:
- [ ] sourcing_result 自动持久化到铜炉 PG
- [ ] quotation 自动持久化
- [ ] data_lineage 记录 session → records 映射
- [ ] 下次采购可通过 data_query 查到"上次买充电宝选了哪家"

**前端**:
- [ ] WS 收到每步的 ui_schema（不要求渲染）
- [ ] 通过 HTTP 推进事件可完成全流程（USER_CONFIRM/USER_SKIP/USER_MODIFY）
- [ ] 每步收到的 `ui_schema` 均通过合约校验（Plan 16）

**可靠性**:
- [ ] trace_id 从 API → Events → PG 全链路贯通
- [ ] 节点失败可重试（模拟 DashScope 超时）
- [ ] 流程中途 abort 可正确终止
