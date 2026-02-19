# Plan 09：铜炉项目骨架与数据基础
> **Task ID**: `09_tonglu_scaffold`
> **目标**: 搭建铜炉服务骨架、PostgreSQL 数据模型、DashScope LLM Service 封装
> **依赖**: `01_project_scaffold`（项目结构就绪）, `03_pg_storage`（PG 环境就绪）
> **预估**: 3 天
> **参考**: `TONGLU_V2_DEV_GUIDE.md` 第 2、3、6 章

---

## 背景

铜炉 v2 作为 TempoOS 的数据服务层，Phase 1 聚焦 CRM 支撑：
- 使用 DashScope 商用 API（Qwen-Plus 为主）
- 简单线性 Pipeline + asyncio（不上 LangGraph）
- 与 TempoOS 同机部署，共享 Redis 和 PostgreSQL

本 Plan 完成铜炉的"地基"：目录结构、配置、数据库表、LLM 调用封装。

---

## 步骤

### 1. 目录结构初始化

在 Monorepo 根目录下创建 `tonglu/` 服务目录：

```
tonglu/
├── __init__.py
├── main.py                  # FastAPI 应用入口 + lifespan
├── config.py                # TongluSettings (pydantic-settings)
├── pipeline/
│   ├── __init__.py
│   └── ingestion.py         # 摄入 Pipeline（Plan 10 实现）
├── query/
│   ├── __init__.py
│   └── engine.py            # 查询引擎（Plan 10 实现）
├── parsers/
│   ├── __init__.py
│   ├── base.py              # BaseParser 抽象
│   ├── pdf_parser.py        # PDF 解析（Plan 10 实现）
│   ├── excel_parser.py      # Excel 解析（Plan 10 实现）
│   └── image_parser.py      # 图片解析（Plan 10 实现）
├── services/
│   ├── __init__.py
│   ├── llm_service.py       # DashScope 封装
│   └── event_sink.py        # Event Sink（Plan 11 实现）
├── storage/
│   ├── __init__.py
│   ├── models.py            # SQLAlchemy 模型
│   ├── database.py          # 数据库连接管理
│   └── repositories.py      # 数据访问层
├── api/
│   ├── __init__.py
│   ├── ingest.py            # 摄入 API（Plan 11 实现）
│   ├── query.py             # 查询 API（Plan 11 实现）
│   └── tasks.py             # 任务状态 API（Plan 11 实现）
└── tests/
    ├── __init__.py
    ├── test_models.py
    ├── test_llm_service.py
    └── conftest.py
```

### 2. 配置模块 (`tonglu/config.py`)

```python
from pydantic_settings import BaseSettings

class TongluSettings(BaseSettings):
    # 服务
    host: str = "0.0.0.0"
    port: int = 8100
    
    # 数据库（与 TempoOS 共享 PG）
    database_url: str = "postgresql+asyncpg://user:pass@localhost:5432/tempoos"
    
    # Redis（与 TempoOS 共享）
    redis_url: str = "redis://localhost:6379/0"
    
    # DashScope
    dashscope_api_key: str = ""
    dashscope_default_model: str = "qwen-plus"
    dashscope_embedding_model: str = "text-embedding-v3"
    dashscope_vl_model: str = "qwen-vl-max"
    
    # 处理
    ingestion_max_concurrent: int = 20
    ingestion_timeout_seconds: int = 120
    
    # Event Sink
    event_sink_enabled: bool = True
    event_sink_persist_rules: str = "sourcing_result,quotation,contract_draft,finance_report"
    
    class Config:
        env_prefix = "TONGLU_"
        env_file = ".env"
```

### 3. PostgreSQL 数据模型 (`tonglu/storage/models.py`)

使用 SQLAlchemy 2.0 声明式风格，创建三层存储表：

```python
from sqlalchemy import Column, String, Text, Enum, ForeignKey, DateTime, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import DeclarativeBase
import uuid

class Base(DeclarativeBase):
    pass

class DataSource(Base):
    """Layer 1: 源数据层 — 原始数据不可变存档"""
    __tablename__ = "tl_data_sources"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    source_type = Column(String(20), nullable=False)  # file / text / url / event
    file_name = Column(String(512))
    content_ref = Column(Text, nullable=False)  # 文件路径或文本内容
    # 注意：SQLAlchemy JSONB 默认值不要用可变对象字面量
    metadata = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class DataRecord(Base):
    """Layer 2: 标准资产层 — LLM 清洗后的结构化资产"""
    __tablename__ = "tl_data_records"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    source_id = Column(UUID(as_uuid=True), ForeignKey("tl_data_sources.id"))
    schema_type = Column(String(64), nullable=False, index=True)
    data = Column(JSONB, nullable=False)
    summary = Column(Text)
    status = Column(String(20), default="processing")  # processing / ready / error / archived
    # 注意：SQLAlchemy JSONB 默认值不要用可变对象字面量
    processing_log = Column(JSONB, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class DataVector(Base):
    """Layer 3: 检索索引层 — 语义向量"""
    __tablename__ = "tl_data_vectors"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    record_id = Column(UUID(as_uuid=True), ForeignKey("tl_data_records.id"), index=True)
    chunk_content = Column(Text, nullable=False)
    embedding = Column(Vector(1024))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

**索引**:
```sql
-- 向量相似度索引（IVFFlat，适合中等数据量）
CREATE INDEX idx_tl_vectors_embedding ON tl_data_vectors 
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- 租户 + 类型复合索引
CREATE INDEX idx_tl_records_tenant_type ON tl_data_records (tenant_id, schema_type);

-- JSONB GIN 索引（支持字段查询）
CREATE INDEX idx_tl_records_data ON tl_data_records USING gin (data);
```

### 4. 数据库连接管理 (`tonglu/storage/database.py`)

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

class Database:
    def __init__(self, database_url: str):
        self.engine = create_async_engine(database_url, pool_size=10, max_overflow=20)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession)
    
    async def init(self):
        """创建表（开发阶段），生产用 Alembic"""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    
    async def close(self):
        await self.engine.dispose()
```

### 5. 数据访问层 (`tonglu/storage/repositories.py`)

```python
class DataRepository:
    """铜炉数据访问层"""
    
    def __init__(self, session_factory):
        self._session_factory = session_factory
    
    async def save_source(self, source: DataSource) -> DataSource: ...
    async def save_record(self, record: DataRecord) -> DataRecord: ...
    async def save_vectors(self, vectors: list[DataVector]) -> None: ...
    
    async def get_record(self, record_id: UUID) -> DataRecord | None: ...
    async def list_records(self, tenant_id: str, schema_type: str = None,
                           offset: int = 0, limit: int = 20,
                           data_filters: dict | None = None) -> list[DataRecord]: ...
    
    async def vector_search(self, embedding: list[float], tenant_id: str,
                            limit: int = 10) -> list[DataRecord]: ...
    
    async def update_record_status(self, record_id: UUID, status: str) -> None: ...

    # Event Sink 去重（建议 Phase 1 就做，避免重复入库）
    async def is_lineage_persisted(self, tenant_id: str, session_id: str, artifact_id: str) -> bool: ...
    async def save_lineage(self, tenant_id: str, session_id: str, artifact_id: str, record_id: UUID) -> None: ...
```

### 6. LLM Service (`tonglu/services/llm_service.py`)

```python
import dashscope

class LLMService:
    """DashScope 统一封装 — 预留 task_type 路由接口"""
    
    MODEL_MAP = {
        "route": "qwen-turbo",         # 类型识别（简单分类）
        "extract": "qwen-plus",         # 字段提取
        "summarize": "qwen-plus",       # 摘要生成
        "validate": "qwen-max",         # 疑难数据 fallback
        "vision": "qwen-vl-max",        # 图片理解
    }
    
    def __init__(self, api_key: str, default_model: str = "qwen-plus"):
        self.api_key = api_key
        self.default_model = default_model
    
    async def call(self, task_type: str, messages: list[dict], **kwargs) -> str:
        """
        统一 LLM 调用入口。
        task_type: 任务类型，Phase 1 用于选择 model name，
                   Phase 2 可扩展为 task_type → model_tier → 具体模型实例
        """
        model = self.MODEL_MAP.get(task_type, self.default_model)
        # 错误重试（指数退避，最多 3 次）
        for attempt in range(3):
            try:
                response = await self._async_call(model, messages, **kwargs)
                return response
            except Exception as e:
                if attempt == 2:
                    raise
                await asyncio.sleep(2 ** attempt)
    
    async def embed(self, texts: list[str], model: str = None) -> list[list[float]]:
        """文本向量化"""
        model = model or "text-embedding-v3"
        response = dashscope.TextEmbedding.call(
            model=model, input=texts, api_key=self.api_key
        )
        return [item["embedding"] for item in response.output["embeddings"]]
    
    async def _async_call(self, model: str, messages: list[dict], **kwargs) -> str:
        """异步调用 DashScope"""
        response = await asyncio.to_thread(
            dashscope.Generation.call,
            model=model,
            messages=messages,
            api_key=self.api_key,
            result_format="message",
            **kwargs,
        )
        if response.status_code != 200:
            raise RuntimeError(f"DashScope error: {response.code} - {response.message}")
        return response.output.choices[0].message.content
```

### 7. FastAPI 应用骨架 (`tonglu/main.py`)

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    settings = TongluSettings()
    app.state.db = Database(settings.database_url)
    await app.state.db.init()
    app.state.llm = LLMService(api_key=settings.dashscope_api_key)
    app.state.repo = DataRepository(app.state.db.session_factory)
    yield
    # Shutdown
    await app.state.db.close()

app = FastAPI(title="铜炉 Tonglu", version="2.0", lifespan=lifespan)

@app.get("/health")
async def health():
    return {"status": "ok", "service": "tonglu"}

# API 路由在 Plan 11 中挂载
# app.include_router(ingest_router, prefix="/api")
# app.include_router(query_router, prefix="/api")
# app.include_router(tasks_router, prefix="/api")
```

### 8. 依赖更新

在项目 `requirements.txt` 中确认以下依赖存在（大部分已有）：

```
# 铜炉新增/确认
pdfplumber          # PDF 解析（Plan 10）
openpyxl            # Excel 解析（Plan 10）
pgvector            # 向量索引（已有）
dashscope           # LLM API（已有）
```

---

## 测试

- `tonglu/tests/test_models.py`:
  - 验证三张表可正常创建
  - 验证 DataSource / DataRecord / DataVector 的 CRUD
  - 验证向量相似度查询（插入测试向量，cosine 检索）
  - 验证 JSONB 字段查询

- `tonglu/tests/test_llm_service.py`:
  - Mock DashScope API，验证 `call()` 方法
  - 验证 task_type → model 映射正确
  - 验证重试逻辑（模拟第 1 次失败、第 2 次成功）
  - 验证 `embed()` 返回正确维度的向量

---

## 验收

- [ ] `tonglu/` 目录结构完整，`main.py` 可启动（`uvicorn tonglu.main:app`）
- [ ] `/health` 接口返回 200
- [ ] 三张 PG 表（`tl_data_sources`, `tl_data_records`, `tl_data_vectors`）自动创建
- [ ] DataRepository 的 CRUD 方法可正常工作
- [ ] LLMService 可调用 DashScope API 并返回结果（需配置 API Key）
- [ ] LLMService 的 task_type 路由映射正确（route→turbo, extract→plus, validate→max）
- [ ] 向量相似度查询可返回结果
- [ ] 所有单元测试通过
