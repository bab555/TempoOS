# Plan 11：铜炉服务化与 TempoOS 集成
> **Task ID**: `11_tonglu_service`
> **目标**: 暴露 HTTP API、实现 Event Sink、完成 TempoOS 侧节点集成、端到端联调
> **依赖**: `08T_full_unit_test_gate`（TempoOS 平台就绪）, `10_tonglu_pipeline`（Pipeline + 查询引擎就绪）
> **预估**: 3 天
> **参考**: `TONGLU_V2_DEV_GUIDE.md` 第 5、7 章

---

## 背景

Plan 09 搭好了骨架，Plan 10 实现了核心处理能力。本 Plan 完成铜炉的"最后一公里"：

1. **HTTP API**: 让外部（包括 TempoOS 节点）可以调用铜炉
2. **Event Sink**: 铜炉自动订阅 TempoOS Redis Bus，持久化 Blackboard 产物
3. **TempoOS 节点**: 在 TempoOS 侧实现 `data_query` / `data_ingest` / `file_parser` 三个内置节点
4. **联调**: 端到端跑通完整链路

---

## 步骤

### 1. 数据摄入 API (`tonglu/api/ingest.py`)

```python
from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks

router = APIRouter(prefix="/api", tags=["ingest"])

@router.post("/ingest/file")
async def ingest_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    tenant_id: str = Form(...),
    schema_type: str = Form(None),
):
    """上传文件，异步处理"""
    # 1. 保存文件到临时目录
    file_path = await _save_upload(file)
    # 2. 生成 task_id
    task_id = str(uuid.uuid4())
    # 3. 后台处理
    background_tasks.add_task(
        _process_file, task_id, file_path, file.filename,
        tenant_id, schema_type
    )
    return {"task_id": task_id, "status": "processing", "message": "文件已接收，正在处理"}

@router.post("/ingest/text")
async def ingest_text(body: IngestTextRequest):
    """直接写入文本/JSON 数据"""
    result = await pipeline.process(
        source_type="text",
        content_ref=json.dumps(body.data),
        tenant_id=body.tenant_id,
        schema_type=body.schema_type,
        metadata=body.metadata,
    )
    return {"record_id": str(result.record_id), "status": result.status}

@router.post("/ingest/batch")
async def ingest_batch(body: IngestBatchRequest):
    """批量摄入（最多 20 个文件）"""
    if len(body.items) > 20:
        raise HTTPException(400, "单次批量最多 20 条")
    results = await pipeline.process_batch(body.items)
    return {"results": [asdict(r) for r in results]}
```

### 2. 数据查询 API (`tonglu/api/query.py`)

```python
router = APIRouter(prefix="/api", tags=["query"])

@router.post("/query")
async def query_data(body: QueryRequest):
    """统一查询接口"""
    results = await query_engine.query(
        intent=body.query,
        mode=body.mode,
        filters=body.filters,
        tenant_id=body.tenant_id,
        limit=body.limit,
    )
    return {"results": results, "count": len(results), "mode": body.mode}

@router.get("/records/{record_id}")
async def get_record(record_id: str):
    """获取单条记录"""
    record = await repo.get_record(UUID(record_id))
    if not record:
        raise HTTPException(404, "Record not found")
    return _serialize_record(record)

@router.get("/records")
async def list_records(
    tenant_id: str,
    schema_type: str = None,
    offset: int = 0,
    limit: int = 20,
):
    """列表查询（分页 + 筛选）"""
    records = await repo.list_records(tenant_id, schema_type, offset, limit)
    return {"records": [_serialize_record(r) for r in records], "offset": offset, "limit": limit}
```

### 3. 任务状态 API (`tonglu/api/tasks.py`)

```python
router = APIRouter(prefix="/api", tags=["tasks"])

# 简单的内存任务状态存储（Phase 1 足够，Phase 2 可改为 Redis/PG）
_task_store: dict[str, dict] = {}

@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """查询处理进度"""
    task = _task_store.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task
```

### 4. 路由挂载 (`tonglu/main.py` 更新)

```python
# 在 main.py 中挂载所有路由
from tonglu.api.ingest import router as ingest_router
from tonglu.api.query import router as query_router
from tonglu.api.tasks import router as tasks_router

app.include_router(ingest_router)
app.include_router(query_router)
app.include_router(tasks_router)
```

### 5. Event Sink (`tonglu/services/event_sink.py`)

铜炉订阅 TempoOS 的 Redis Bus，自动持久化 Blackboard 产物。

**对齐 TempoOS 现状（重要）**：
- TempoOS 的事件总线是 **tenant-scoped Pub/Sub channel**，格式为 `tempo:{tenant_id}:events`（见 `tempo_os/kernel/namespace.py#get_channel`）
- Bus 消息体是 `TempoEvent` 的 JSON（见 `tempo_os/protocols/schema.py`），不是随意 dict
- 当前 TempoOS 的 `EVENT_RESULT` payload **不包含** `artifact_keys`
- Blackboard 的 artifact 存储键规则是：
  - artifact 内容：`tempo:{tenant_id}:artifact:{artifact_id}`（String JSON）
  - session 的 artifact 列表：`tempo:{tenant_id}:session:{session_id}:artifacts`（Set）
  - artifact 的 `artifact_id` 就是 NodeResult 的 artifacts key（见 `tempo_os/core/context.py` 写入逻辑）

因此 Event Sink 的落地做法是：监听某些“足以标识 session 活跃/完成”的事件（如 `STATE_TRANSITION`、`EVENT_RESULT`、`EVENT_ERROR` 等），然后去 Redis 读取该 session 当前的 artifact 列表，并按 `persist_rules` 过滤、去重后持久化。

```python
import redis.asyncio as aioredis

class EventSinkListener:
    """订阅 TempoOS EVENT_RESULT，自动持久化 Blackboard 产物"""
    
    def __init__(self, redis_url: str, pipeline: IngestionPipeline,
                 persist_rules: list[str]):
        self._redis_url = redis_url
        self._pipeline = pipeline
        self._persist_rules = set(persist_rules)
        self._running = False
    
    async def start(self):
        """启动事件监听（后台 asyncio task）"""
        self._running = True
        self._redis = aioredis.from_url(self._redis_url)
        self._pubsub = self._redis.pubsub()
        # 注意：TempoOS 的 bus channel 是 tenant-scoped：
        #   tempo:{tenant_id}:events
        # Phase 1 简化：配置要监听的 tenant_id 列表；每个 tenant 一个 listener
        for tenant_id in self._tenant_ids:
            await self._pubsub.subscribe(f"tempo:{tenant_id}:events")
        
        while self._running:
            message = await self._pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message and message["type"] == "message":
                await self._handle_event(json.loads(message["data"]))
    
    async def _handle_event(self, event: dict):
        """处理单个 TempoEvent（dict 形式）"""
        tenant_id = event.get("tenant_id")
        session_id = event.get("session_id")
        if not tenant_id or not session_id:
            return

        # 触发条件：只要是该 session 的关键事件就检查一次
        if event.get("type") not in ("EVENT_RESULT", "EVENT_ERROR", "STATE_TRANSITION", "STEP_DONE"):
            return

        # 读取该 session 当前的 artifact 列表（TempoOS: Redis Set）
        artifacts_set_key = f"tempo:{tenant_id}:session:{session_id}:artifacts"
        artifact_ids = await self._redis.smembers(artifacts_set_key)
        for art_id in artifact_ids:
            artifact_id = art_id.decode() if isinstance(art_id, bytes) else art_id
            if not self._match_rules(artifact_id):
                continue

            # 读取 artifact 内容（TempoOS: String JSON）
            artifact_key = f"tempo:{tenant_id}:artifact:{artifact_id}"
            raw = await self._redis.get(artifact_key)
            if not raw:
                continue

            # 去重策略（Phase 1 简化）：
            # - 方案 A：在铜炉 PG 建 `tl_data_lineage`，用 (tenant_id, session_id, artifact_id) 做唯一约束
            # - 方案 B：在 artifact metadata 中标记已入库 record_id（侵入性更强，不推荐）
            #
            # 这里按方案 A 设计：若已存在 lineage 记录则跳过（repo 层实现）
            if await self._repo.is_lineage_persisted(tenant_id, session_id, artifact_id):
                continue

            payload = raw.decode() if isinstance(raw, bytes) else raw
            result = await self._pipeline.process(
                source_type="event",
                content_ref=payload,
                tenant_id=tenant_id,
                metadata={
                    "session_id": session_id,
                    "artifact_id": artifact_id,
                    "source": "event_sink",
                    "trigger_event_type": event.get("type"),
                    "trigger_event_id": event.get("id"),
                },
            )
            await self._repo.save_lineage(tenant_id, session_id, artifact_id, result.record_id)
    
    def _match_rules(self, key: str) -> bool:
        """检查 artifact_key 是否匹配持久化规则"""
        return key in self._persist_rules
    
    async def stop(self):
        """优雅关闭"""
        self._running = False
        if self._pubsub:
            await self._pubsub.unsubscribe()
        if self._redis:
            await self._redis.close()
```

在 `tonglu/main.py` 的 lifespan 中启动：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... 已有的初始化 ...
    
    # 启动 Event Sink
    if settings.event_sink_enabled:
        persist_rules = settings.event_sink_persist_rules.split(",")
        app.state.event_sink = EventSinkListener(
            redis_url=settings.redis_url,
            pipeline=app.state.pipeline,
            persist_rules=persist_rules,
        )
        asyncio.create_task(app.state.event_sink.start())
    
    yield
    
    # 关闭 Event Sink
    if hasattr(app.state, "event_sink"):
        await app.state.event_sink.stop()
    # ... 已有的清理 ...
```

### 6. TempoOS 侧：铜炉 HTTP Client (`tempo_os/runtime/tonglu_client.py`)

```python
import httpx

class TongluClient:
    """铜炉 HTTP API 客户端"""
    
    def __init__(self, base_url: str = "http://localhost:8100"):
        self._base_url = base_url
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)
    
    async def query(self, intent: str, filters: dict = None,
                    tenant_id: str = None, mode: str = "hybrid") -> list[dict]:
        """语义检索 + 字段聚合"""
        resp = await self._client.post("/api/query", json={
            "query": intent, "mode": mode,
            "filters": filters or {}, "tenant_id": tenant_id,
        })
        resp.raise_for_status()
        return resp.json()["results"]
    
    async def ingest(self, data: dict, tenant_id: str,
                     schema_type: str = None) -> str:
        """主动写入数据 → 返回 record_id"""
        resp = await self._client.post("/api/ingest/text", json={
            "data": data, "tenant_id": tenant_id, "schema_type": schema_type,
        })
        resp.raise_for_status()
        return resp.json()["record_id"]
    
    async def upload(self, file_path: str, file_name: str,
                     tenant_id: str, schema_type: str = None) -> str:
        """文件上传 → 返回 task_id"""
        with open(file_path, "rb") as f:
            resp = await self._client.post("/api/ingest/file", files={
                "file": (file_name, f),
            }, data={
                "tenant_id": tenant_id,
                "schema_type": schema_type or "",
            })
        resp.raise_for_status()
        return resp.json()["task_id"]
    
    async def get_record(self, record_id: str) -> dict:
        """直读单条记录"""
        resp = await self._client.get(f"/api/records/{record_id}")
        resp.raise_for_status()
        return resp.json()
    
    async def get_task(self, task_id: str) -> dict:
        """查询处理进度"""
        resp = await self._client.get(f"/api/tasks/{task_id}")
        resp.raise_for_status()
        return resp.json()
    
    async def close(self):
        await self._client.aclose()
```

### 7. TempoOS 侧：数据查询节点 (`tempo_os/nodes/data_query.py`)

```python
class DataQueryNode(BaseNode):
    node_id = "data_query"
    name = "数据查询"
    
    def __init__(self, tonglu_client: TongluClient):
        self.tonglu = tonglu_client
    
    async def execute(self, session_id, tenant_id, params, blackboard):
        results = await self.tonglu.query(
            intent=params["intent"],
            filters=params.get("filters"),
            tenant_id=tenant_id,
            mode=params.get("mode", "hybrid"),
        )
        return NodeResult(
            status="success",
            result={"records": results, "count": len(results)},
            artifacts={"query_result": results},
            ui_schema=self._build_table_schema(results),
        )
    
    def _build_table_schema(self, results: list[dict]) -> dict:
        if not results:
            return {"components": [{"type": "text", "props": {"content": "未找到数据"}}]}
        columns = [{"key": k, "title": k} for k in results[0].keys() if not k.startswith("_")]
        return {"components": [{"type": "table", "props": {"columns": columns, "data": results}}]}
```

### 8. TempoOS 侧：数据写入节点 (`tempo_os/nodes/data_ingest.py`)

```python
class DataIngestNode(BaseNode):
    node_id = "data_ingest"
    name = "数据写入"
    
    def __init__(self, tonglu_client: TongluClient):
        self.tonglu = tonglu_client
    
    async def execute(self, session_id, tenant_id, params, blackboard):
        data = params.get("data")
        if not data and params.get("artifact_key"):
            data = await blackboard.get_artifact(params["artifact_key"])
        
        record_id = await self.tonglu.ingest(
            data=data, tenant_id=tenant_id,
            schema_type=params.get("schema_type"),
        )
        return NodeResult(
            status="success",
            result={"record_id": record_id},
        )
```

### 9. TempoOS 侧：文件解析节点 (`tempo_os/nodes/file_parser.py`)

```python
class FileParserNode(BaseNode):
    node_id = "file_parser"
    name = "文件解析"
    
    def __init__(self, tonglu_client: TongluClient):
        self.tonglu = tonglu_client
    
    async def execute(self, session_id, tenant_id, params, blackboard):
        # 1. 上传文件到铜炉
        task_id = await self.tonglu.upload(
            file_path=params["file_path"],
            file_name=params.get("file_name", ""),
            tenant_id=tenant_id,
            schema_type=params.get("schema_type"),
        )
        
        # 2. 轮询等待处理完成（最多 120 秒）
        record = await self._wait_for_result(task_id, timeout=120)
        
        return NodeResult(
            status="success",
            result=record,
            artifacts={"parsed_data": record},
        )
    
    async def _wait_for_result(self, task_id: str, timeout: int = 120) -> dict:
        """轮询铜炉任务状态"""
        import asyncio
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            task = await self.tonglu.get_task(task_id)
            if task["status"] == "ready":
                return await self.tonglu.get_record(task["record_id"])
            elif task["status"] == "error":
                raise RuntimeError(f"文件处理失败: {task.get('error')}")
            await asyncio.sleep(2)
        raise TimeoutError(f"文件处理超时 ({timeout}s)")
```

### 10. TempoOS 节点注册

在 `tempo_os/main.py` 中注册三个数据节点：

```python
# 在 lifespan 或 startup 中
tonglu_client = TongluClient(base_url=os.getenv("TONGLU_URL", "http://localhost:8100"))

registry.register_builtin("data_query", DataQueryNode(tonglu_client))
registry.register_builtin("data_ingest", DataIngestNode(tonglu_client))
registry.register_builtin("file_parser", FileParserNode(tonglu_client))
```

### 11. 集成测试与联调

**测试场景 1: 文件上传全链路**
1. 通过 TempoOS 的 `file_parser` 节点上传一份 PDF
2. 验证铜炉收到文件 → 解析 → LLM 提取 → 向量化 → 入库
3. 通过 `data_query` 节点用自然语言查到刚入库的数据

**测试场景 2: Event Sink 自动沉淀**
1. 在 TempoOS 中运行一个工作流，产生 `quotation` artifact
2. 验证铜炉 Event Sink 自动收到事件
3. 验证 Blackboard 产物被持久化到铜炉 PG
4. 通过铜炉 API 可查到该数据

**测试场景 3: 批量并发**
1. 同时上传 30 个文件到铜炉
2. 验证实际并行不超过 20 个（观察 semaphore）
3. 验证所有文件最终处理完成，无丢失

**测试场景 4: 错误恢复**
1. 上传一个损坏的 PDF
2. 验证该文件 status="error"，不影响其他文件处理
3. 验证错误信息记录在 processing_log 中

---

## 测试

### 铜炉侧

- `tonglu/tests/test_api_ingest.py`: 文件上传、文本写入、批量摄入 API
- `tonglu/tests/test_api_query.py`: 查询接口（SQL/Vector/Hybrid）
- `tonglu/tests/test_event_sink.py`:
  - Mock Redis 事件，验证 Event Sink 触发
  - 验证 Blackboard 产物被持久化
  - 验证非匹配 key 被忽略

### TempoOS 侧

- `tests/unit/test_tonglu_client.py`: Mock HTTP 测试 client 各方法
- `tests/unit/test_data_query_node.py`: 查询节点执行 + ui_schema 生成
- `tests/unit/test_data_ingest_node.py`: 写入节点（params 传入 / Blackboard 读取）
- `tests/unit/test_file_parser_node.py`: 文件解析 + 轮询等待逻辑

### 集成测试

- `tests/integration/test_tonglu_e2e.py`: 完整链路（上传 → 处理 → 查询）
- `tests/integration/test_event_sink_e2e.py`: 工作流产物 → Event Sink → 铜炉入库

---

## 验收

### 铜炉 API

- [ ] `POST /api/ingest/file` 上传文件，返回 task_id，后台异步处理
- [ ] `POST /api/ingest/text` 直接写入文本数据，返回 record_id
- [ ] `POST /api/ingest/batch` 批量摄入，最多 20 条
- [ ] `POST /api/query` 支持 sql / vector / hybrid 三种模式
- [ ] `GET /api/records/{id}` 返回单条记录
- [ ] `GET /api/records` 支持分页和 schema_type 筛选
- [ ] `GET /api/tasks/{task_id}` 返回处理进度

### Event Sink

- [ ] 铜炉启动后自动订阅 TempoOS Redis Bus
- [ ] 收到 EVENT_RESULT 后，匹配 persist_rules 的 artifact 被自动持久化
- [ ] 非匹配 key 被忽略，不产生多余写入
- [ ] 铜炉重启后重新订阅，不丢事件

### TempoOS 节点

- [ ] `builtin://data_query` 可在工作流中查询铜炉数据，返回 ui_schema（表格）
- [ ] `builtin://data_ingest` 可将 params 或 Blackboard 产物写入铜炉
- [ ] `builtin://file_parser` 可上传文件到铜炉并等待解析结果
- [ ] 三个节点在 TempoOS 节点注册表中可见

### 端到端

- [ ] 完整链路：TempoOS 上传文件 → 铜炉解析入库 → TempoOS 查询到数据
- [ ] Event Sink 链路：工作流产物 → 自动沉淀 → 可查询
- [ ] 20 文件并行处理不超时、不丢数据
- [ ] 所有单元测试和集成测试通过
