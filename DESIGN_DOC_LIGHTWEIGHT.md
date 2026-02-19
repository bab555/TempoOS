# 数字员工轻量化业务方案 — 需求 + 后端能力 + 接口设计

> **版本**: v2.0
> **日期**: 2026-02-15
> **核心理念**: LLM 原生能力 (联网搜索 + 文本生成) + TempoOS 流程编排 + Tonglu 企业知识库 + 前端渲染落地

---

## 一、原始业务需求

以下 6 项需求来自实际业务场景，覆盖采购全链路：

| # | 需求 | 输入 | 期望输出 |
|---|------|------|----------|
| 1 | **采购寻源与比价** | 产品名称/规格/数量 | 多供应商对比表（价格、好评、规格、资质） |
| 2 | **内部报价匹配** | 客户需求清单 | 按四局商城 SKU 规格生成的报价表 |
| 3 | **模板制表** | 任意表格模板 + 关键词 | 自动采集价格并填充模板表格 |
| 4 | **合同生成** | 报价清单 + 合同模板 | 按模板生成的采购合同 |
| 5 | **送货单生成** | 合同内容 + 送货单模板 | 按模板生成的送货单 |
| 6 | **财务报表与对账** | 时间范围 + 合同/发票数据 | 月报、年报、对账单、发票核对结果 |

---

## 二、架构决策

### 2.1 关键决策

| 决策点 | 方案 | 理由 |
|--------|------|------|
| 外部数据获取 | **DashScope 联网搜索** (`enable_search=True`) | 无需开发爬虫，大模型自带全网搜索能力 |
| 文件生成 (docx/xlsx) | **前端负责** | 后端只返回 JSON 数据，前端用 `docx.js` / `xlsx` 等库渲染 |
| 模板处理 | **后端解析模板文本 → LLM 理解并填充** | 用户上传模板，Tonglu 解析为文本，LLM 识别占位符并生成填充数据 |
| 后端原子能力 | **Search Worker + Writer Worker** | 两个 Worker 覆盖全部 6 个需求 |

### 2.2 整体数据流

```
用户操作 (前端)
    │
    ├── 1. 发起任务 ──→ POST /api/workflow/start
    │                     ├── node_id: "search"  (联网搜索)
    │                     └── node_id: "writer"  (撰写/填充)
    │
    ├── 2. 上传模板 ──→ POST /api/ingest/file  (Tonglu)
    │                     └── 返回 file_id + 识别出的模板字段
    │
    ├── 3. 实时进度 ←── WebSocket /ws/events/{session_id}
    │                     └── TempoEvent (STEP_DONE / NEED_USER_INPUT)
    │
    └── 4. 接收结果 ←── NodeResult.result (JSON 数据)
                          └── 前端渲染为表格/文档，提供"导出"按钮
```

---

## 三、后端提供的核心能力

### 3.1 新增 Node 定义

基于 TempoOS 现有的 `BaseNode` 抽象基类，新增两个业务节点。

#### A. SearchNode — 联网搜索节点

```python
# 位置: tempo_os/nodes/search.py

class SearchNode(BaseNode):
    """
    联网搜索节点 — 利用 DashScope 大模型的联网搜索能力获取外部信息。

    通过 DashScope API 的 enable_search=True 参数，
    让大模型自行搜索全网信息并返回结构化结果。
    """

    node_id = "search"
    name = "联网搜索"
    description = "利用大模型联网搜索能力获取外部信息（价格、供应商、规格等）"
    param_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索指令（自然语言）"
            },
            "output_format": {
                "type": "string",
                "enum": ["table", "text", "json"],
                "default": "table",
                "description": "期望的输出格式"
            },
            "context": {
                "type": "string",
                "description": "补充上下文（如：之前的搜索结果摘要）"
            },
        },
        "required": ["query"],
    }
```

**execute() 签名** (严格遵循 `BaseNode` 规范):

```python
async def execute(
    self,
    session_id: str,
    tenant_id: str,
    params: Dict[str, Any],
    blackboard: TenantBlackboard,
) -> NodeResult:
```

**返回的 NodeResult**:

```python
NodeResult(
    status="success",
    result={
        "type": "table",                    # 前端据此选择渲染组件
        "title": "ThinkPad X1 Carbon 全网比价",
        "columns": [
            {"key": "supplier", "label": "供应商"},
            {"key": "price",    "label": "价格"},
            {"key": "rating",   "label": "好评率"},
            {"key": "spec",     "label": "规格型号"},
            {"key": "cert",     "label": "资质"},
        ],
        "rows": [
            {"supplier": "XX旗舰店", "price": "9999", "rating": "98%", ...},
            ...
        ],
    },
    artifacts={"search_result": <同上 result>},  # 写入 Blackboard
    ui_schema={
        "components": [{
            "type": "table",
            "props": {"columns": [...], "data": [...]}
        }]
    },
)
```

**覆盖需求**: 需求 1 (比价)、需求 3 (采集价格)。

---

#### B. WriterNode — 智能撰写节点

```python
# 位置: tempo_os/nodes/writer.py

class WriterNode(BaseNode):
    """
    智能撰写节点 — 根据动态 Skill Prompt 完成各类文档撰写任务。

    核心机制：通过 params.skill 加载不同的 System Prompt，
    使同一个 Node 能够完成报价、合同、送货单、报表等不同任务。
    """

    node_id = "writer"
    name = "智能撰写"
    description = "根据指定技能(skill)和数据，生成报价表/合同/送货单/报表等结构化内容"
    param_schema = {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "enum": [
                    "quotation",       # 报价表
                    "contract",        # 合同
                    "delivery_note",   # 送货单
                    "financial_report",# 财务报表
                    "comparison",      # 对比分析
                    "general",         # 通用撰写
                ],
                "description": "撰写技能类型，决定使用哪个 System Prompt"
            },
            "instruction": {
                "type": "string",
                "description": "用户的具体撰写指令"
            },
            "data": {
                "type": "object",
                "description": "直接传入的业务数据（如报价清单 JSON）"
            },
            "artifact_keys": {
                "type": "array",
                "items": {"type": "string"},
                "description": "从 Blackboard 读取的 artifact key 列表"
            },
            "template_id": {
                "type": "string",
                "description": "Tonglu 中的模板记录 ID（用户上传的模板）"
            },
            "tonglu_query": {
                "type": "string",
                "description": "需要从 Tonglu 检索的补充数据查询语句"
            },
            "output_format": {
                "type": "string",
                "enum": ["document_fill", "table", "text", "report"],
                "default": "document_fill",
                "description": "期望的输出格式"
            },
        },
        "required": ["skill", "instruction"],
    }
```

**execute() 内部逻辑**:

```python
async def execute(self, session_id, tenant_id, params, blackboard) -> NodeResult:
    # 1. 加载 Skill Prompt
    system_prompt = self._load_skill_prompt(params["skill"])

    # 2. 收集上下文数据
    context_parts = []

    #    2a. 从 Blackboard 读取前序节点产物
    for key in params.get("artifact_keys", []):
        artifact = await blackboard.get_artifact(key)
        if artifact:
            context_parts.append(f"[数据: {key}]\n{json.dumps(artifact, ensure_ascii=False)}")

    #    2b. 从 Tonglu 检索内部数据（如商城 SKU、历史合同）
    if params.get("tonglu_query"):
        tonglu_data = await self._tonglu.query(
            intent=params["tonglu_query"],
            tenant_id=tenant_id,
        )
        context_parts.append(f"[内部数据]\n{json.dumps(tonglu_data, ensure_ascii=False)}")

    #    2c. 读取用户上传的模板
    if params.get("template_id"):
        template = await self._tonglu.get_record(params["template_id"])
        context_parts.append(f"[模板内容]\n{template.get('summary', '')}\n{json.dumps(template.get('data', {}), ensure_ascii=False)}")

    #    2d. 直接传入的数据
    if params.get("data"):
        context_parts.append(f"[业务数据]\n{json.dumps(params['data'], ensure_ascii=False)}")

    # 3. 组装 messages 并调用 LLM
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{params['instruction']}\n\n{''.join(context_parts)}"},
    ]
    llm_response = await self._llm_call(messages)

    # 4. 解析 LLM 输出为结构化 JSON
    result_data = self._parse_output(llm_response, params.get("output_format", "document_fill"))

    return NodeResult(
        status="success",
        result=result_data,
        artifacts={f"writer_{params['skill']}": result_data},
    )
```

**覆盖需求**: 需求 2 (报价)、需求 4 (合同)、需求 5 (送货单)、需求 6 (报表)。

---

### 3.2 Skill Prompt 机制

WriterNode 的核心在于 **Skill Prompt** — 不同的 `skill` 参数加载不同的 System Prompt：

| skill | System Prompt 核心指令 | 输出格式 |
|-------|----------------------|----------|
| `quotation` | "你是报价专员。根据客户需求和内部商品库数据，生成报价表。输出 JSON table 格式。" | `table` |
| `contract` | "你是合同专员。根据报价数据和模板，生成合同各章节内容。输出 JSON document_fill 格式。" | `document_fill` |
| `delivery_note` | "你是物流专员。根据合同明细，生成送货单。输出 JSON document_fill 格式。" | `document_fill` |
| `financial_report` | "你是财务专员。根据合同和发票数据，生成对账报表。输出 JSON report 格式。" | `report` |
| `comparison` | "你是分析师。对比多个数据源，生成对比分析表。输出 JSON table 格式。" | `table` |
| `general` | "你是文档助手。根据用户指令完成撰写任务。" | 按指令 |

Skill Prompt 存储位置建议: `tempo_os/nodes/skills/` 目录下，每个 skill 一个 `.txt` 或 `.yaml` 文件，便于运营人员调优而无需改代码。

---

### 3.3 模板上传与解析流程

用户上传的 Word/Excel 模板通过 Tonglu 处理：

```
前端: 用户点击"上传模板" → POST /api/ingest/file (Tonglu)
                                │
Tonglu Pipeline:                │
  1. 文件存入 data_sources      ←┘
  2. 解析器提取文本内容 (pdfplumber / openpyxl / python-docx)
  3. LLM 识别模板结构:
     - 哪些是固定文本（如"采购合同"标题）
     - 哪些是占位符（如"甲方: ___"）
     - 哪些是表格区域
  4. 生成 data_record:
     schema_type = "template"
     data = {
       "template_type": "contract",
       "fixed_sections": ["标题", "条款一", ...],
       "fillable_fields": ["party_a", "party_b", "date", "total_amount"],
       "table_regions": ["product_list"],
       "raw_text": "完整模板文本..."
     }
     summary = "采购合同标准模板，含甲乙方信息、商品明细表、付款条款"
  5. 向量化 summary → data_vectors (支持语义检索模板)
```

**前端拿到 `template_id` 后**，在发起 Writer 任务时传入 `template_id` 参数即可。

---

### 3.4 现有能力复用

以下 TempoOS / Tonglu 现有组件**无需修改**，直接复用：

| 组件 | 位置 | 用途 |
|------|------|------|
| `BaseNode` + `NodeResult` | `tempo_os/nodes/base.py` | 新节点继承此基类 |
| `TenantBlackboard` | `tempo_os/memory/blackboard.py` | 节点间数据传递 (artifacts) |
| `TempoEvent` | `tempo_os/protocols/schema.py` | 事件总线消息格式 |
| 事件常量 | `tempo_os/protocols/events.py` | `STEP_DONE`, `NEED_USER_INPUT` 等 |
| `NodeRegistry` | `tempo_os/kernel/node_registry.py` | 注册 `search` 和 `writer` 节点 |
| `SessionManager` | `tempo_os/kernel/session_manager.py` | 会话生命周期管理 |
| `PlatformContext` | `tempo_os/core/context.py` | `execute_node()` / `dispatch_step()` |
| WebSocket 推送 | `tempo_os/api/ws.py` | `/ws/events/{session_id}` 实时进度 |
| Workflow API | `tempo_os/api/workflow.py` | `POST /start`, `POST /{id}/event`, `GET /{id}/state` |
| `TongluClient` | `tempo_os/runtime/tonglu_client.py` | Writer 节点调用 Tonglu 查询/获取模板 |
| Tonglu Ingest API | `tonglu/api/ingest.py` | 模板上传 (`POST /api/ingest/file`) |
| Tonglu Query API | `tonglu/api/query.py` | 内部数据检索 (`POST /api/query`) |
| `QueryEngine` | `tonglu/query/engine.py` | SQL + Vector 混合查询 |
| `LLMService` | `tonglu/services/llm_service.py` | DashScope 调用封装 |
| `EventSinkListener` | `tonglu/services/event_sink.py` | 自动沉淀 Writer 产物到 Tonglu |

---

## 四、接口设计

### 4.0 Agent Controller — 中控对话入口 (新增)

前端的**唯一入口**是 `POST /api/agent/chat`。该接口内部由 LLM 自动决策调用哪个 Node。

- **实现文件**: `tempo_os/api/agent.py`
- **SSE 工具**: `tempo_os/api/sse.py`
- **路由注册**: `tempo_os/main.py` → `app.include_router(agent_router, prefix="/api")`
- **Headers**: `X-Tenant-Id` (必传) + `X-User-Id` (前端生成的 UUID)
- **响应格式**: SSE 流式 (`text/event-stream`)
- **事件类型**: `session_init` / `thinking` / `message` / `tool_start` / `tool_done` / `ui_render` / `error` / `done`

Agent Controller 内部调用 `PlatformContext.execute_node()` 执行具体 Node，复用现有基础设施。

### 4.1 任务执行 — Workflow API (内部/高级用途)

以下接口保留用于内部测试和高级编排场景，前端正常使用时不需要直接调用。

#### 4.1.1 联网搜索任务

**POST** `/api/workflow/start`

**Headers**: `X-Tenant-Id: default`

**Request Body** (对应 `StartRequest` schema):
```json
{
  "node_id": "search",
  "params": {
    "query": "淘宝上搜索 ThinkPad X1 Carbon Gen11，对比3家店铺的价格、好评率、规格和资质",
    "output_format": "table"
  }
}
```

**Response** (对应 `StartResponse` schema):
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "state": "done",
  "flow_id": null,
  "ui_schema": {
    "components": [{
      "type": "table",
      "props": {
        "columns": [
          {"key": "supplier", "title": "供应商"},
          {"key": "price",    "title": "价格"},
          {"key": "rating",   "title": "好评率"},
          {"key": "spec",     "title": "规格型号"},
          {"key": "cert",     "title": "资质情况"}
        ],
        "data": [
          {"supplier": "联想官方旗舰店", "price": "9,299", "rating": "98%", "spec": "i7-1365U/16G/512G", "cert": "官方授权"},
          {"supplier": "京东自营", "price": "9,499", "rating": "97%", "spec": "i7-1365U/16G/1T", "cert": "品牌直供"},
          {"supplier": "XX数码专营", "price": "8,899", "rating": "92%", "spec": "i7-1365U/16G/512G", "cert": "一般纳税人"}
        ]
      }
    }]
  }
}
```

#### 4.1.2 智能撰写任务 (以合同生成为例)

**POST** `/api/workflow/start`

**Headers**: `X-Tenant-ID: default`

**Request Body**:
```json
{
  "node_id": "writer",
  "params": {
    "skill": "contract",
    "instruction": "根据以下报价清单，按照上传的合同模板生成采购合同",
    "template_id": "rec_abc123",
    "data": {
      "party_a": "中建四局XX项目部",
      "party_b": "XX供应商",
      "items": [
        {"name": "ThinkPad X1", "spec": "i7/16G/512G", "qty": 50, "unit_price": 9299},
        {"name": "Dell U2723QE", "spec": "27寸 4K", "qty": 50, "unit_price": 3299}
      ]
    },
    "output_format": "document_fill"
  }
}
```

**Response**:
```json
{
  "session_id": "660e8400-xxxx",
  "state": "done",
  "flow_id": null,
  "ui_schema": {
    "components": [{
      "type": "document",
      "props": {
        "type": "document_fill",
        "template_id": "rec_abc123",
        "fields": {
          "contract_no": "CG-20260215-001",
          "party_a": "中建四局XX项目部",
          "party_b": "XX供应商",
          "sign_date": "2026年2月15日",
          "total_amount": "629,900.00",
          "total_amount_cn": "陆拾贰万玖仟玖佰元整",
          "delivery_date": "2026年3月15日",
          "payment_terms": "货到验收合格后30日内付款"
        },
        "tables": {
          "product_list": [
            {"name": "ThinkPad X1", "spec": "i7/16G/512G", "qty": 50, "unit_price": 9299, "amount": 464950},
            {"name": "Dell U2723QE", "spec": "27寸 4K", "qty": 50, "unit_price": 3299, "amount": 164950}
          ]
        }
      }
    }]
  }
}
```

#### 4.1.3 内部报价匹配

**POST** `/api/workflow/start`

```json
{
  "node_id": "writer",
  "params": {
    "skill": "quotation",
    "instruction": "根据客户需求清单，匹配四局商城的标准SKU，生成报价表",
    "data": {
      "customer": "XX项目部",
      "requirements": [
        {"description": "办公笔记本电脑，i7处理器，16G内存", "qty": 50},
        {"description": "27寸4K显示器", "qty": 50},
        {"description": "黑色签字笔 0.5mm", "qty": 500}
      ]
    },
    "tonglu_query": "四局商城 笔记本 显示器 签字笔",
    "output_format": "table"
  }
}
```

#### 4.1.4 财务报表

**POST** `/api/workflow/start`

```json
{
  "node_id": "writer",
  "params": {
    "skill": "financial_report",
    "instruction": "生成2026年1月的采购对账单，核对合同金额与发票金额",
    "tonglu_query": "2026年1月 采购合同 发票",
    "output_format": "report"
  }
}
```

---

### 4.2 模板管理 — 复用 Tonglu 现有 API

#### 上传模板

**POST** `/api/ingest/file` (Tonglu 已有接口)

**Request**: `multipart/form-data`

| 字段 | 类型 | 说明 |
|------|------|------|
| `file` | File | Word/Excel 模板文件 |
| `tenant_id` | string | 租户 ID |
| `schema_type` | string | 固定传 `"template"` |

**Response**:
```json
{
  "task_id": "uuid",
  "status": "processing",
  "message": "文件已接收，正在处理"
}
```

处理完成后通过 `GET /api/tasks/{task_id}` 获取结果，拿到 `record_id` 即为 `template_id`。

#### 查询模板

**GET** `/api/records?tenant_id=default&schema_type=template` (Tonglu 已有接口)

返回所有已上传的模板列表。

---

### 4.3 实时进度 — 复用 WebSocket

**WebSocket** `/ws/events/{session_id}?tenant_id=default` (TempoOS 已有接口)

前端连接后，会收到 TempoEvent 格式的实时推送：

```json
{
  "id": "uuid",
  "type": "STEP_DONE",
  "source": "node.search",
  "target": "*",
  "tenant_id": "default",
  "session_id": "550e8400-xxxx",
  "tick": 1,
  "payload": {
    "node_id": "search",
    "status": "success",
    "message": "搜索完成，找到3家供应商"
  },
  "created_at": 1739577600.0,
  "priority": 5
}
```

---

### 4.4 多步工作流 (可选扩展)

对于需要 **先搜索再撰写** 的复合任务（如"搜索比价后自动生成合同"），可定义 YAML Flow：

```yaml
# flows/examples/search_then_contract.yaml
name: search_then_contract
description: "先搜索比价，用户确认后生成合同"
states:
  - searching
  - review_results       # 用户审核搜索结果
  - writing_contract
  - review_contract      # 用户审核合同
  - done
initial_state: searching
transitions:
  - { from: searching,        event: STEP_DONE,     to: review_results }
  - { from: review_results,   event: USER_CONFIRM,  to: writing_contract }
  - { from: review_results,   event: USER_MODIFY,   to: searching }
  - { from: writing_contract, event: STEP_DONE,     to: review_contract }
  - { from: review_contract,  event: USER_CONFIRM,  to: done }
  - { from: review_contract,  event: USER_MODIFY,   to: writing_contract }
state_node_map:
  searching: "builtin://search"
  writing_contract: "builtin://writer"
user_input_states:
  - review_results
  - review_contract
```

触发方式:

```json
POST /api/workflow/start
{
  "flow_id": "search_then_contract",
  "params": {
    "query": "搜索3款i7笔记本比价",
    "skill": "contract",
    "template_id": "rec_abc123"
  }
}
```

此 Flow 完全复用 TempoOS 现有的 `FlowDefinition`、`TempoFSM`、`SessionManager` 机制，
通过 `user_input_states` 实现 Human-in-the-loop（用户确认后才继续）。

---

## 五、前后端数据交互协议 (JSON Output Schema)

后端通过 `NodeResult.result` 返回以下标准 JSON 格式，前端据此选择渲染组件。

### 5.1 表格类 (`type: "table"`)

用于：比价表、报价单、商品清单。

```json
{
  "type": "table",
  "title": "笔记本比价表",
  "columns": [
    {"key": "supplier", "label": "供应商"},
    {"key": "price",    "label": "价格",   "type": "number"},
    {"key": "rating",   "label": "好评率"},
    {"key": "spec",     "label": "规格型号"},
    {"key": "cert",     "label": "资质",   "type": "tag"}
  ],
  "rows": [
    {"supplier": "联想旗舰店", "price": 9299, "rating": "98%", "spec": "i7/16G/512G", "cert": "官方授权"},
    {"supplier": "京东自营",   "price": 9499, "rating": "97%", "spec": "i7/16G/1T",   "cert": "品牌直供"}
  ],
  "summary": "推荐选择联想旗舰店，性价比最优。"
}
```

### 5.2 文档填充类 (`type: "document_fill"`)

用于：合同、送货单 — 前端拿到数据后回填到本地模板。

```json
{
  "type": "document_fill",
  "template_id": "rec_abc123",
  "fields": {
    "contract_no": "CG-20260215-001",
    "party_a": "中建四局XX项目部",
    "party_b": "XX供应商",
    "sign_date": "2026年2月15日",
    "total_amount": "629,900.00",
    "total_amount_cn": "陆拾贰万玖仟玖佰元整"
  },
  "tables": {
    "product_list": [
      {"name": "ThinkPad X1", "spec": "i7/16G/512G", "qty": 50, "unit_price": 9299, "amount": 464950}
    ]
  },
  "sections": [
    {"title": "付款条款", "content": "货到验收合格后30日内，甲方以银行转账方式支付全部货款。"},
    {"title": "违约责任", "content": "..."}
  ]
}
```

### 5.3 报表类 (`type: "report"`)

用于：月报、年报、对账单。

```json
{
  "type": "report",
  "title": "2026年1月采购对账单",
  "period": {"start": "2026-01-01", "end": "2026-01-31"},
  "summary_metrics": [
    {"label": "合同总额",   "value": "1,250,000.00", "unit": "元"},
    {"label": "已开发票",   "value": "980,000.00",   "unit": "元"},
    {"label": "未开发票",   "value": "270,000.00",   "unit": "元", "alert": true},
    {"label": "合同数量",   "value": "12",           "unit": "份"}
  ],
  "detail_table": {
    "columns": [
      {"key": "contract_no", "label": "合同编号"},
      {"key": "supplier",    "label": "供应商"},
      {"key": "amount",      "label": "合同金额", "type": "number"},
      {"key": "invoiced",    "label": "已开票",   "type": "number"},
      {"key": "gap",         "label": "差额",     "type": "number"},
      {"key": "status",      "label": "状态",     "type": "tag"}
    ],
    "rows": [
      {"contract_no": "CG-001", "supplier": "A公司", "amount": 500000, "invoiced": 500000, "gap": 0,      "status": "已结清"},
      {"contract_no": "CG-002", "supplier": "B公司", "amount": 750000, "invoiced": 480000, "gap": 270000, "status": "待开票"}
    ]
  }
}
```

### 5.4 纯文本类 (`type: "text"`)

用于：通用问答、简单说明。

```json
{
  "type": "text",
  "content": "根据分析，推荐选择A供应商，理由如下：\n1. 价格最低...\n2. 好评率最高..."
}
```

---

## 六、节点注册 (对齐 main.py 规范)

新节点在 `tempo_os/main.py` 的 `_register_builtin_nodes()` 中注册：

```python
# 在现有导入后添加
from tempo_os.nodes.search import SearchNode
from tempo_os.nodes.writer import WriterNode

def _register_builtin_nodes(ctx) -> None:
    # ... 现有节点 ...

    # 业务 Agent 节点
    nodes.extend([
        SearchNode(),                          # node_id = "search"
        WriterNode(tonglu_client=tonglu_client),  # node_id = "writer"
    ])
```

注册后，这两个节点即可通过以下方式使用：
- **单步调用**: `POST /api/workflow/start` + `node_id: "search"` 或 `"writer"`
- **Flow 编排**: YAML 中引用 `builtin://search` 或 `builtin://writer`
- **WebSocket 推送**: 执行过程中自动通过 `/ws/events/{session_id}` 推送进度

---

## 七、需求 → 接口映射速查表

| 需求 | 调用方式 | node_id | skill | 关键参数 |
|------|----------|---------|-------|----------|
| 1. 采购比价 | 单步 | `search` | — | `query`, `output_format: "table"` |
| 2. 内部报价 | 单步 | `writer` | `quotation` | `data` (需求清单), `tonglu_query` (商城SKU) |
| 3. 模板制表 | 单步 | `search` → `writer` | `general` | `query`, `template_id` |
| 4. 合同生成 | 单步 | `writer` | `contract` | `template_id`, `data` (报价数据) |
| 5. 送货单 | 单步 | `writer` | `delivery_note` | `template_id`, `artifact_keys` (合同数据) |
| 6. 财务报表 | 单步 | `writer` | `financial_report` | `tonglu_query` (合同+发票) |

---

## 八、Tonglu 知识库数据准备

为支撑业务，需要向 Tonglu 导入以下数据：

| 数据类型 (`schema_type`) | 来源 | 用途 |
|--------------------------|------|------|
| `mall_sku` | 四局商城产品导出 Excel | 需求 2: 内部报价匹配 |
| `template` | 用户上传的合同/送货单模板 | 需求 3/4/5: 模板填充 |
| `contract_purchase` | 历史采购合同 | 需求 6: 财务对账 |
| `contract_sales` | 历史销售合同 | 需求 6: 财务对账 |
| `invoice` | 发票记录 | 需求 6: 发票核对 |

导入方式：通过 Tonglu 现有的 `POST /api/ingest/file` 或 `POST /api/ingest/batch` 接口。

---

## 九、开发任务清单

| 序号 | 任务 | 涉及文件 | 状态 | 预估 |
|------|------|----------|------|------|
| 0a | ~~Agent Controller (SSE + Tool Use)~~ | `tempo_os/api/agent.py` | **已完成** | — |
| 0b | ~~SSE 工具模块~~ | `tempo_os/api/sse.py` | **已完成** | — |
| 0c | ~~deps.py 增加 X-User-Id~~ | `tempo_os/api/deps.py` | **已完成** | — |
| 0d | ~~main.py 注册 Agent 路由~~ | `tempo_os/main.py` | **已完成** | — |
| 1 | 实现 `SearchNode` | `tempo_os/nodes/search.py` | 待开发 | 1 天 |
| 2 | 实现 `WriterNode` + Skill Prompt 机制 | `tempo_os/nodes/writer.py`, `tempo_os/nodes/skills/*.txt` | 待开发 | 2 天 |
| 3 | 在 `main.py` 注册新业务节点 | `tempo_os/main.py` | 待开发 | 0.5 小时 |
| 4 | LLMService 增加 `enable_search` 支持 | `tonglu/services/llm_service.py` | 待开发 | 0.5 天 |
| 5 | Tonglu URL Ingestor (OSS 拉取 + VL 直连) | `tonglu/pipeline/ingestion.py` | 待开发 | 1 天 |
| 6 | Tonglu 模板解析增强 (识别占位符) | `tonglu/pipeline/ingestion.py` | 待开发 | 1 天 |
| 7 | 编写 Skill Prompt 文件 | `tempo_os/nodes/skills/` | 待开发 | 1 天 |
| 8 | 导入四局商城数据到 Tonglu | 数据准备 + 脚本 | 待开发 | 0.5 天 |
| 9 | 示例 YAML Flow (搜索→合同) | `flows/examples/` | 待开发 | 0.5 天 |
| 10 | 集成测试 | `tests/` | 待开发 | 1 天 |
| **合计** | | | | **约 8.5 天** |
