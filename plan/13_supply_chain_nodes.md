# Plan 13：供应链业务节点迁入
> **Task ID**: `13_supply_chain_nodes`
> **目标**: 从 nexus-supply 迁入 sourcing/quoting/doc_writer/finance 四个业务节点
> **依赖**: `06_node_framework`, `12_model_gateway`
> **源代码**: `d:\项目\数字员工\nexus-supply\backend\app\agents\subgraphs\`
> **预估**: 3 天

---

## 迁移原则

1. **只迁执行逻辑**，不迁 LangGraph 状态管理（由平台 FSM 替代）
2. **不迁 RedisContextManager**（由 Blackboard 替代）
3. **LLM 调用统一走平台 DashScope 封装**（不直接用 `aliyun_llm.llm_service`）
4. **DB 查询保留**（供应链业务库表作为节点的"外部数据源"）

---

## 步骤

### 1. 供应链数据库模型迁入

从 `nexus-supply/backend/app/models/db_models.py` 复制业务表模型：
- `Product`、`Partner`、`Contract`、`Transaction`、`Template`
- 放置到 `tempo_os/nodes/biz/supply_models.py`
- 这些表保持独立（供应链业务库），不混入平台核心表

### 2. builtin://sourcing 节点

创建 `tempo_os/nodes/biz/sourcing.py`：

**源**: `nexus-supply` 的 `sourcing_agent.py` 中 `execute_search_node()`

**改造要点**:
- 输入：`params = { keywords, quantity?, budget?, platforms? }`
- 执行：调用平台 DashScope（`self.dashscope.chat()` + 联网搜索）
- 输出：`NodeResult` 包含搜索结果 + ui_schema（表格+选择列表）
- 产物：`artifacts = {"sourcing_result": recommendations}`
- 去除 LangGraph `SourcingState` 管理（由 FSM + Blackboard 替代）

### 3. builtin://quoting 节点

创建 `tempo_os/nodes/biz/quoting.py`：

**源**: `nexus-supply` 的 `quoting_agent.py` 中 `match_node()` + `generate_node()`

**改造要点**:
- 输入：`params = { items: [{name, quantity, specs}], customer_name? }`
- 执行：
  1. 从 Blackboard 读取 `sourcing_result`（如果有前序步骤）
  2. 查询供应链业务库匹配商品（保留原 SQLAlchemy 查询逻辑）
  3. 生成报价数据
- 输出：`NodeResult` + ui_schema（报价表格 + 操作按钮）
- 产物：`artifacts = {"quotation": quotation_data}`

### 4. builtin://doc_writer 节点

创建 `tempo_os/nodes/biz/doc_writer.py`：

**源**: `nexus-supply` 的 `writer_agent.py` 中 `select_template_node()` + `generate_node()` + `convert_node()`

**改造要点**:
- 输入：`params = { document_type, party_a, party_b, amount, items, ... }`
- 执行：
  1. 从 Blackboard 读取前序产物（报价数据、供应商信息等）
  2. 查模板库（保留 SQLAlchemy 查询）
  3. 调 DashScope 生成文书 Markdown
  4. 转 DOCX（保留 `document_generator` 能力）
- 输出：`NodeResult` + ui_schema（Markdown 预览 + 下载按钮）
- 产物：`artifacts = {"contract_draft": content, "document_url": url}`

### 5. builtin://finance 节点

创建 `tempo_os/nodes/biz/finance.py`：

**源**: `nexus-supply` 的 `finance_agent.py` 中 `query_node()` + `aggregate_node()` + `generate_report_node()`

**改造要点**:
- 输入：`params = { report_type, start_date, end_date, category? }`
- 执行：
  1. 查询 Transaction 表（保留 SQLAlchemy 查询）
  2. 程序聚合统计
  3. 调 DashScope 生成报表 Markdown
- 输出：`NodeResult` + ui_schema（KPI 卡片 + 表格 + 图表）
- 产物：`artifacts = {"finance_report": report_data}`

### 6. 采购全流程 YAML

创建 `flows/examples/procurement_flow.yaml`：

```yaml
name: procurement_flow
description: "完整采购流程：寻源→报价→签约→记账"
states: [sourcing, sourcing_done, quoting, quoting_done, contracting, contract_done, finance, end]
initial_state: sourcing
state_node_map:
  sourcing:    builtin://sourcing
  quoting:     builtin://quoting
  contracting: builtin://doc_writer
  finance:     builtin://finance
user_input_states: [sourcing_done, quoting_done, contract_done]
transitions:
  - { from: sourcing,      event: STEP_DONE,    to: sourcing_done }
  - { from: sourcing_done, event: USER_CONFIRM, to: quoting }
  - { from: sourcing_done, event: USER_SKIP,    to: contracting }
  - { from: quoting,       event: STEP_DONE,    to: quoting_done }
  - { from: quoting_done,  event: USER_CONFIRM, to: contracting }
  - { from: contracting,   event: STEP_DONE,    to: contract_done }
  - { from: contract_done, event: USER_CONFIRM, to: finance }
  - { from: finance,       event: STEP_DONE,    to: end }
```

### 7. 测试

- `tests/unit/test_sourcing_node.py`：搜索节点（Mock DashScope）
- `tests/unit/test_quoting_node.py`：报价节点（Mock DB）
- `tests/unit/test_doc_writer_node.py`：文书节点
- `tests/unit/test_finance_node.py`：财务节点
- `tests/e2e/test_procurement_flow.py`：采购全流程端到端

### 8. 验收

- [ ] 4 个供应链节点注册成功
- [ ] 每个节点可独立执行并返回 ui_schema
- [ ] 数据通过 Blackboard 在节点间正确流转
- [ ] procurement_flow YAML 可加载并完整执行
- [ ] 不依赖 nexus-supply 的 RedisContextManager、LangGraph、master_agent
