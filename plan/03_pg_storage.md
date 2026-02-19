# Plan 03：PostgreSQL 持久存储层
> **Task ID**: `03_pg_storage`
> **目标**: 建立 PG 持久层，支持 sessions/flows/events/幂等日志的持久化与查询
> **依赖**: `02_tempo_core_migration`
> **预估**: 2 天

---

## 步骤

### 1. 数据库连接管理

创建 `tempo_os/storage/database.py`：
- 使用 SQLAlchemy 2.0 async engine（`create_async_engine`）
- 连接池配置（pool_size=20，从 config 读取）
- `async_sessionmaker` 工厂
- `get_db()` FastAPI 依赖注入
- lifespan 钩子：启动时 `SELECT 1` 验证连接，关闭时 `engine.dispose()`

### 2. 模型定义

创建 `tempo_os/storage/models.py`，定义 SQLAlchemy ORM 模型：

**workflow_sessions**：
- session_id (UUID PK)、tenant_id、flow_id、current_state、session_state
- params (JSONB)、created_at、updated_at、completed_at、ttl_seconds

**workflow_flows**：
- flow_id (VARCHAR PK)、name、description、yaml_content (TEXT)、param_schema (JSONB)

**workflow_events**（审计日志，可回放）：
- event_id (UUID PK)、tenant_id、session_id (FK)
- event_type、source、target、tick、trace_id、priority
- from_state、to_state、payload (JSONB)、created_at
- 索引：(tenant_id, session_id, created_at)

**idempotency_log**：
- (session_id, step, attempt) 联合主键
- status、result_hash、created_at

**registry_nodes**：
- node_id (VARCHAR PK)、node_type ('builtin'|'webhook')
- name、description、endpoint、param_schema (JSONB)、status

### 3. 数据库初始化脚本

创建 `tempo_os/storage/init_db.py`：
- 使用 `Base.metadata.create_all(engine)` 自动建表
- 或生成 Alembic 初始迁移脚本

创建 `scripts/init_db.sql`：
- 纯 SQL 版本（参考 MASTER_PLAN 附录 A），便于 DBA 手动执行

### 4. Repository 层

创建 `tempo_os/storage/repositories.py`：

```python
class SessionRepository:
    async def create(tenant_id, flow_id, params) -> session_id
    async def get(session_id) -> WorkflowSession
    async def update_state(session_id, new_state, session_state)
    async def list_by_tenant(tenant_id, limit, offset)

class FlowRepository:
    async def create(flow_id, name, yaml_content)
    async def get(flow_id) -> WorkflowFlow
    async def list_all()

class EventRepository:
    async def append(event: TempoEvent, from_state, to_state)
    async def list_by_session(session_id, limit)
    async def replay(session_id) -> list[events]   # 事件回放

class IdempotencyRepository:
    async def check(session_id, step, attempt) -> exists?
    async def record(session_id, step, attempt, status, result_hash)

class NodeRegistryRepository:
    async def register(node_id, node_type, name, endpoint, param_schema)
    async def get(node_id)
    async def list_all(node_type_filter=None)
```

### 5. 测试

创建 `tests/integration/test_pg_storage.py`：
- 使用测试 PG（或 SQLite in-memory 做快速验证）
- 测试 CRUD 全流程
- 测试事件回放
- 测试幂等检查

### 6. 验收

- [ ] 数据库可自动建表
- [ ] Session CRUD 正常
- [ ] Event append + replay 正常
- [ ] Idempotency check/record 正常
- [ ] Node 注册/查询正常
