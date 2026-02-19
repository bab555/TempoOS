# 铜炉 v2 (Tonglu Rebirth) 开发指南

> **定位**: TempoOS 的数据服务层 — 智能 CRM 数据中台
> **核心职责**: 为 TempoOS 工作流提供**数据摄入、存储、检索、关联**能力
> **版本**: v2.0
> **阶段**: 三阶段渐进式交付

---

## 1. 项目概述

**铜炉 (Tonglu)** 是 TempoOS 数字员工统一平台的**数据服务层**。它为平台上运行的各类业务 Agent（供应链、CAD、文档等）提供统一的数据管理能力。

### 与 TempoOS 的关系

```
TempoOS（工作流引擎 + 业务节点）
    │
    ├── 产生数据 ──→ 铜炉摄入（文件解析、字段提取、向量化）
    ├── 消费数据 ←── 铜炉查询（语义检索、SQL 聚合、关联推理）
    └── 事件驱动 ←→ Redis Bus（EVENT_RESULT → 自动持久化）
```

铜炉**不是**独立产品，而是 TempoOS 生态的一部分。TempoOS 的节点通过 HTTP API 或 Redis Bus 与铜炉交互，业务 Agent 无需关心数据如何存储和检索。

### 核心能力（按阶段交付）

| 能力 | 阶段 | 说明 |
|------|------|------|
| 文件解析与字段提取 | Phase 1 | PDF/Excel/图片 → 结构化 JSON |
| 数据标准化与入库 | Phase 1 | 清洗、去重、写入 PG |
| 语义检索 | Phase 1 | 向量化 + 自然语言查数 |
| TempoOS 节点集成 | Phase 1 | data_query / data_ingest / file_parser |
| 事件沉淀（Event Sink） | Phase 1 | 自动持久化 Blackboard 产物 |
| 实体关联与知识图谱 | Phase 2 | 三元组关系、跨实体推理 |
| 模型分层与本地部署 | Phase 2 | Light/Medium/Heavy 三级模型 |
| 数据质量治理 | Phase 3 | 自动校验、评分、修正 |
| 多模态深度理解 | Phase 3 | Qwen-VL 视觉理解、音频转写 |

---

## 2. 系统架构

### 2.1 Phase 1 架构（当前目标）

Phase 1 聚焦 **TempoOS CRM 支撑**，使用商用 LLM API（DashScope），不涉及本地模型部署。

```mermaid
graph TD
    subgraph "TempoOS 平台"
        Nodes["业务节点<br/>(data_query / data_ingest / file_parser)"]
        Bus[Redis Event Bus]
        BB[Blackboard]
    end

    subgraph "铜炉 Tonglu (FastAPI 服务)"
        API[HTTP API]
        Sink[Event Sink<br/>自动持久化]
        
        subgraph "处理层"
            Parser[文件解析器<br/>PDF/Excel/Image]
            LLM[LLM 字段提取<br/>DashScope API]
            Embed[向量化<br/>text-embedding-v3]
        end
        
        subgraph "存储层 (PostgreSQL + pgvector)"
            Raw[data_sources<br/>原始文件]
            Std[data_records<br/>结构化资产]
            Vec[data_vectors<br/>语义索引]
        end
        
        API --> Parser
        Parser --> LLM
        LLM --> Std
        LLM --> Embed
        Embed --> Vec
        API --> Raw
    end

    Nodes -->|HTTP| API
    Bus -->|EVENT_RESULT| Sink
    Sink -->|读取产物| BB
    Sink --> Std
```

### 2.2 技术栈

| 组件 | Phase 1 选型 | Phase 2+ 扩展 |
|------|-------------|--------------|
| 语言 | Python 3.11+ | — |
| Web 框架 | FastAPI | — |
| 数据库 | PostgreSQL 15+ (pgvector, pg_trgm) | — |
| LLM | DashScope API (Qwen-Plus / Qwen-Max) | + Ollama 本地模型 (7B/14B) |
| Embedding | DashScope text-embedding-v3 | + 本地 bge-large |
| VLM | DashScope Qwen-VL-Max（按需调用） | + 本地 Qwen-VL |
| 编排 | 简单 Pipeline（asyncio） | LangGraph 状态图 |
| 基础设施 | Redis (共享 TempoOS 实例) | + Docker Compose 独立部署 |

### 2.3 Phase 1 设计约束

- **并发能力**: 支持约 **20 个文件并行处理**（asyncio + Semaphore 控制）
- **LLM 调用**: 全部走 DashScope 商用 API，不依赖本地 GPU
- **部署方式**: 与 TempoOS 同机部署，共享 Redis 和 PostgreSQL
- **数据规模**: 面向中小企业 CRM 场景，预期数据量 1 万~10 万条级别

---

## 3. 数据模型 (Three-Layer Storage)

铜炉采用 **"Raw → Standard → Index"** 三层存储策略，确保数据可追溯、可利用、可检索。

### Layer 1: 源数据层 (`data_sources`)

**定位**: 原始数据的不可变存档，用于追溯和重新处理。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID | 主键 |
| `tenant_id` | VARCHAR | 租户标识（对应 TempoOS 租户） |
| `source_type` | ENUM | `file` / `text` / `url` / `event` |
| `file_name` | VARCHAR | 原始文件名（如有） |
| `content_ref` | TEXT | 文件存储路径或文本内容 |
| `metadata` | JSONB | 来源信息（上传者、来源系统、session_id 等） |
| `created_at` | TIMESTAMP | 创建时间 |

### Layer 2: 标准资产层 (`data_records`)

**定位**: 经过 LLM 清洗、标准化的业务资产，是铜炉的**核心层**。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID | 主键 |
| `tenant_id` | VARCHAR | 租户标识 |
| `source_id` | FK | → `data_sources.id` |
| `schema_type` | VARCHAR | 数据类型：`invoice` / `contract` / `contact` / `quotation` / `meeting_note` 等 |
| `data` | JSONB | 结构化字段，如 `{"amount": 100, "party_a": "华为"}` |
| `summary` | TEXT | LLM 生成的语义摘要 |
| `status` | ENUM | `processing` / `ready` / `error` / `archived` |
| `processing_log` | JSONB | 处理过程日志 |
| `created_at` | TIMESTAMP | 创建时间 |
| `updated_at` | TIMESTAMP | 更新时间 |

### Layer 3: 检索索引层 (`data_vectors`)

**定位**: 为语义检索服务。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID | 主键 |
| `record_id` | FK | → `data_records.id` |
| `chunk_content` | TEXT | 文本切片内容 |
| `embedding` | VECTOR(1024) | 向量表示 |
| `created_at` | TIMESTAMP | 创建时间 |

### 预留：实体关系层 (`entity_relations`) — Phase 2

| 字段 | 类型 | 说明 |
|------|------|------|
| `subject` | VARCHAR | 主体实体（如 "华为"） |
| `predicate` | VARCHAR | 关系（如 "采购"） |
| `object` | VARCHAR | 客体实体（如 "服务器"） |
| `source_record_id` | FK | → `data_records.id` |

### 预留：数据血缘层 (`data_lineage`) — Phase 1 后期

| 字段 | 类型 | 说明 |
|------|------|------|
| `session_id` | VARCHAR | TempoOS 工作流会话 ID |
| `artifact_key` | VARCHAR | Blackboard 中的 key |
| `record_id` | FK | → `data_records.id` |
| `tenant_id` | VARCHAR | 租户标识 |
| `created_at` | TIMESTAMP | 创建时间 |

---

## 4. 核心处理流程

### 4.1 数据摄入 Pipeline（Phase 1）

Phase 1 采用**简单线性 Pipeline**（非 LangGraph），用 asyncio 控制并发。

```
文件/文本输入
    │
    ▼
┌─────────────────┐
│  1. 格式识别     │  根据文件扩展名/MIME 类型判断
│     (规则引擎)   │  PDF → pdf_parser, Excel → excel_parser, Image → vlm_parser
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  2. 内容提取     │  PDF: pdfplumber / PyMuPDF
│     (解析器)     │  Excel: openpyxl
│                  │  Image: DashScope Qwen-VL-Max
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  3. 字段提取     │  DashScope Qwen-Plus
│     (LLM)       │  Prompt: "按 {schema} 提取字段，生成摘要"
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  4. 向量化       │  DashScope text-embedding-v3
│     (Embedding)  │  对摘要和关键字段进行向量化
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  5. 持久化       │  并行写入：
│     (Storage)    │  data_sources + data_records + data_vectors
└─────────────────┘
```

**并发控制**:

```python
class IngestionPipeline:
    """数据摄入流水线"""
    
    def __init__(self, max_concurrent: int = 20):
        self._semaphore = asyncio.Semaphore(max_concurrent)
    
    async def process(self, source: RawSource) -> DataRecord:
        async with self._semaphore:
            # 1. 格式识别 → 选择解析器
            parser = self._get_parser(source.source_type, source.file_name)
            # 2. 内容提取
            raw_text = await parser.extract(source)
            # 3. LLM 字段提取 + 摘要
            record = await self._llm_extract(raw_text, source)
            # 4. 向量化
            vectors = await self._embed(record.summary)
            # 5. 持久化
            await self._save(source, record, vectors)
            return record
    
    async def process_batch(self, sources: list[RawSource]) -> list[DataRecord]:
        """批量处理，受 semaphore 控制并发上限"""
        tasks = [self.process(s) for s in sources]
        return await asyncio.gather(*tasks, return_exceptions=True)
```

### 4.2 数据查询（Phase 1）

Phase 1 提供三种查询模式：

| 模式 | 说明 | 示例 |
|------|------|------|
| `sql` | 精确字段查询 | "金额大于 100 万的合同" |
| `vector` | 语义相似检索 | "和智慧园区相关的项目" |
| `hybrid` | SQL + 向量混合 | "华为的合同中提到服务器的" |

```python
class QueryEngine:
    """统一查询引擎"""
    
    async def query(self, intent: str, mode: str = "hybrid",
                    filters: dict = None, tenant_id: str = None) -> list[dict]:
        if mode == "sql":
            return await self._sql_query(intent, filters, tenant_id)
        elif mode == "vector":
            return await self._vector_query(intent, tenant_id)
        else:  # hybrid
            sql_results = await self._sql_query(intent, filters, tenant_id)
            vec_results = await self._vector_query(intent, tenant_id)
            return self._merge_and_rank(sql_results, vec_results)
```

### 4.3 Event Sink — TempoOS 产物自动沉淀（Phase 1）

铜炉订阅 TempoOS 的 Redis Bus，自动将工作流产物持久化。

```python
class EventSinkListener:
    """订阅 TempoOS EVENT_RESULT，自动持久化 Blackboard 产物"""
    
    # 需要持久化的 artifact key 规则（可配置）
    PERSIST_RULES = [
        "sourcing_result", "quotation", "contract_draft",
        "finance_report", "equipment_list", "document_final",
    ]
    
    async def on_event(self, event: dict):
        artifact_keys = event.get("artifact_keys", [])
        for key in artifact_keys:
            if self._match_rules(key):
                # 从 Blackboard (Redis) 读取产物
                data = await self._read_blackboard(event["tenant_id"], 
                                                    event["session_id"], key)
                # 走摄入 pipeline 持久化
                await self.pipeline.process(RawSource(
                    source_type="event",
                    content=data,
                    metadata={"session_id": event["session_id"],
                              "artifact_key": key}
                ))
```

---

## 5. 接口定义 (API)

### 5.1 数据摄入

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ingest/file` | 上传文件（PDF/Image/Excel），异步处理 |
| POST | `/api/ingest/text` | 上传文本/JSON |
| POST | `/api/ingest/batch` | 批量摄入（最多 20 个文件） |

**请求示例** (`/api/ingest/file`):
```
POST /api/ingest/file
Content-Type: multipart/form-data

file: <binary>
tenant_id: "tenant_001"
schema_type: "quotation"  (可选，不传则自动识别)
```

**响应**:
```json
{
  "task_id": "uuid",
  "status": "processing",
  "message": "文件已接收，正在处理"
}
```

### 5.2 数据查询

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/query` | 统一查询接口 |
| GET | `/api/records/{id}` | 获取单条记录 |
| GET | `/api/records` | 列表查询（分页 + 筛选） |

**请求示例** (`/api/query`):
```json
{
  "query": "查找所有和智慧园区相关的合同，且金额大于100万",
  "mode": "hybrid",
  "tenant_id": "tenant_001",
  "filters": {"schema_type": "contract"},
  "limit": 20
}
```

### 5.3 任务状态

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/tasks/{task_id}` | 查询处理进度 |

### 5.4 TempoOS 节点调用接口

以下接口专为 TempoOS 内置节点（`data_query` / `data_ingest` / `file_parser`）设计：

| TempoOS 节点 | 调用的铜炉接口 | 说明 |
|--------------|---------------|------|
| `builtin://data_query` | `POST /api/query` | 语义检索 + 字段聚合 |
| `builtin://data_ingest` | `POST /api/ingest/text` | 主动写入数据 |
| `builtin://file_parser` | `POST /api/ingest/file` + `GET /api/records/{id}` | 文件上传 + 获取解析结果 |

---

## 6. LLM 调用策略

### 6.1 Phase 1：商用 API 统一调用

Phase 1 全部使用 DashScope 商用 API，按任务类型选择模型：

| 任务 | 模型 | 说明 |
|------|------|------|
| 字段提取 | Qwen-Plus | 性价比最优，¥0.004/千 Token |
| 摘要生成 | Qwen-Plus | 基础能力足够 |
| 数据类型识别 | Qwen-Turbo | 简单分类，¥0.002/千 Token |
| 图片/扫描件理解 | Qwen-VL-Max | 视觉任务必须用 VLM |
| 疑难数据处理 | Qwen-Max | 仅在 Plus 处理失败时 fallback |
| 文本向量化 | text-embedding-v3 | Embedding 专用，成本极低 |

**成本估算**（Phase 1，以 Qwen-Plus 为主）:

| 数据量 | 预估 Token | Qwen-Plus 成本 | Qwen-Max fallback (5%) | 总成本 |
|--------|-----------|---------------|----------------------|--------|
| 100 条 | 88 万 | ¥3.5 | ¥0.9 | ~¥4.4 |
| 1,000 条 | 880 万 | ¥35 | ¥9 | ~¥44 |
| 10,000 条 | 8,800 万 | ¥352 | ¥88 | ~¥440 |

### 6.2 Phase 2 扩展：模型分层（预留设计）

Phase 2 引入本地模型，大幅降低成本：

```
┌─────────────────────────────────────────────────┐
│                  LLM Router                      │
│  根据任务复杂度自动选择模型（用户无感知）           │
├─────────────────────────────────────────────────┤
│                                                  │
│  Light (7B, 本地 Ollama)                         │
│  → 类型识别、标准化、简单摘要                      │
│  → 成本: ¥0                                      │
│                                                  │
│  Medium (14B-32B, 本地或云端 Plus)                │
│  → 字段提取、实体关联、复杂摘要                    │
│  → 成本: 低                                      │
│                                                  │
│  Heavy (72B+ / 云端 Max)                         │
│  → 数据校验、纠错、复杂推理                       │
│  → 成本: 高但调用量极少                           │
│                                                  │
└─────────────────────────────────────────────────┘
```

Phase 1 的代码需要为此预留接口：

```python
class LLMService:
    """LLM 调用服务 — Phase 1 统一走 DashScope"""
    
    async def call(self, task_type: str, messages: list[dict], **kwargs) -> str:
        """
        task_type 用于未来模型路由，Phase 1 仅用于选择 model name。
        Phase 2 可扩展为: task_type → model_tier → 具体模型实例
        """
        model = self._select_model(task_type)
        return await self._dashscope_call(model, messages, **kwargs)
    
    def _select_model(self, task_type: str) -> str:
        # Phase 1: 简单映射
        MODEL_MAP = {
            "route": "qwen-turbo",
            "extract": "qwen-plus",
            "summarize": "qwen-plus",
            "validate": "qwen-max",
            "vision": "qwen-vl-max",
        }
        return MODEL_MAP.get(task_type, "qwen-plus")
```

---

## 7. 与 TempoOS 的集成点

### 7.1 TempoOS 侧需要实现的组件

| 组件 | 位置 | 说明 |
|------|------|------|
| `TongluClient` | `tempo_os/runtime/tonglu_client.py` | 铜炉 HTTP 客户端 |
| `DataQueryNode` | `tempo_os/nodes/data_query.py` | 数据查询节点 |
| `DataIngestNode` | `tempo_os/nodes/data_ingest.py` | 数据写入节点 |
| `FileParserNode` | `tempo_os/nodes/file_parser.py` | 文件解析节点 |

### 7.2 铜炉侧需要实现的组件

| 组件 | 位置 | 说明 |
|------|------|------|
| FastAPI 应用 | `tonglu/main.py` | HTTP 服务入口 |
| 摄入 Pipeline | `tonglu/pipeline/ingestion.py` | 文件解析 → LLM 提取 → 向量化 → 入库 |
| 查询引擎 | `tonglu/query/engine.py` | SQL + 向量混合查询 |
| Event Sink | `tonglu/services/event_sink.py` | Redis Bus 事件监听与持久化 |
| LLM Service | `tonglu/services/llm_service.py` | DashScope 封装（预留模型路由） |
| DB Models | `tonglu/storage/models.py` | SQLAlchemy 模型定义 |
| 文件解析器 | `tonglu/parsers/` | PDF / Excel / Image 解析器 |

### 7.3 共享基础设施

| 资源 | 说明 |
|------|------|
| Redis | 共享 TempoOS 的 Redis 实例（Event Bus + Blackboard 读取） |
| PostgreSQL | 共享 PG 实例，铜炉使用独立的 schema 或表前缀 `tl_` |
| DashScope API Key | 共享配置（`.env` 中的 `DASHSCOPE_API_KEY`） |

---

## 8. 开发路线图

### Phase 1：TempoOS CRM 支撑（当前阶段）

> **目标**: 铜炉作为 TempoOS 的数据服务层可用，支撑智能 CRM 场景。
> **LLM**: 全部使用 DashScope 商用 API
> **并发**: 20 文件并行处理

| 步骤 | 任务 | 依赖 | 预估 |
|------|------|------|------|
| 1.1 | 铜炉项目骨架（`tonglu/` 目录、FastAPI、配置） | TempoOS Phase 1 完成 | 1 天 |
| 1.2 | PostgreSQL 模型（data_sources / data_records / data_vectors） | 1.1 | 1 天 |
| 1.3 | 文件解析器（PDF / Excel / Image 基础解析） | 1.1 | 2 天 |
| 1.4 | LLM Service（DashScope 封装，预留 task_type 路由接口） | 1.1 | 1 天 |
| 1.5 | 摄入 Pipeline（解析 → 提取 → 向量化 → 入库，20 并发） | 1.2, 1.3, 1.4 | 2 天 |
| 1.6 | 查询引擎（SQL + 向量 + 混合模式） | 1.2 | 2 天 |
| 1.7 | HTTP API（ingest / query / records / tasks） | 1.5, 1.6 | 1 天 |
| 1.8 | Event Sink（订阅 Redis Bus，自动持久化 Blackboard 产物） | 1.7 | 1 天 |
| 1.9 | TempoOS 节点集成（TongluClient + 三个内置节点） | 1.7 | 1 天 |
| 1.10 | 集成测试 + 联调 | 1.8, 1.9 | 2 天 |

**Phase 1 验收标准**:
- [ ] 上传 PDF/Excel/图片，铜炉自动解析并入库
- [ ] 通过自然语言查询铜炉数据，返回结构化结果
- [ ] TempoOS 工作流中可使用 `data_query` / `data_ingest` / `file_parser` 节点
- [ ] TempoOS 流程完成后，Blackboard 产物自动沉淀到铜炉
- [ ] 20 个文件并行处理不超时、不丢数据

### Phase 2：能力增强（扩展阶段）

> **目标**: 引入本地模型降低成本，增加实体关联和知识图谱能力。

| 任务 | 说明 |
|------|------|
| LLM Router | 实现三级模型分层（Light/Medium/Heavy），支持 Ollama 本地模型 |
| 实体关联 | `entity_relations` 表 + 三元组提取 + 跨实体查询 |
| LangGraph 编排 | 将线性 Pipeline 升级为 LangGraph 状态图（支持条件分支、质量回退） |
| 数据血缘 | `data_lineage` 表完善，支持追溯"数据从哪个流程产生" |
| 批量导入优化 | 支持万级数据批量导入，队列化处理 |

### Phase 3：数据治理（远期扩展）

> **目标**: 铜炉具备独立的数据治理能力，可脱离 CRM 场景通用化。

| 任务 | 说明 |
|------|------|
| 数据质量评分 | LLM 自省 + 规则引擎，自动评估数据完整性和一致性 |
| 自动纠错 | 发现错误后自动修正（需 Heavy 模型） |
| 多模态深度理解 | Qwen-VL 本地部署，支持复杂图纸/扫描件的深度解析 |
| Schema 自动发现 | LLM 自动识别新数据类型并生成 schema 模板 |
| 数据资产目录 | 可视化数据资产全景，支持数据血缘追踪 |

---

## 9. 配置项

### 铜炉配置（`tonglu/config.py` 或 `.env`）

```env
# 服务
TONGLU_HOST=0.0.0.0
TONGLU_PORT=8100

# 数据库（与 TempoOS 共享 PG 实例）
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/tempoos

# Redis（与 TempoOS 共享）
REDIS_URL=redis://localhost:6379/0

# DashScope
DASHSCOPE_API_KEY=sk-xxx
DASHSCOPE_DEFAULT_MODEL=qwen-plus
DASHSCOPE_EMBEDDING_MODEL=text-embedding-v3

# 处理
INGESTION_MAX_CONCURRENT=20
INGESTION_TIMEOUT_SECONDS=120

# Event Sink
EVENT_SINK_ENABLED=true
EVENT_SINK_PERSIST_RULES=sourcing_result,quotation,contract_draft,finance_report
```

---

> **开发原则**:
> 1. **Phase 1 务实优先** — 用商用 API 快速跑通，不过度设计
> 2. **接口预留扩展** — LLM 调用通过 `task_type` 路由，为 Phase 2 模型分层留口子
> 3. **与 TempoOS 松耦合** — 铜炉通过 HTTP API 和 Redis Bus 交互，不直接依赖 TempoOS 内部代码
> 4. **数据不可变** — 原始数据（data_sources）一旦写入不可修改，所有变更产生新版本
