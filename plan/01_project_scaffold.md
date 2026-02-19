# Plan 01：项目骨架、依赖、配置与测试框架
> **Task ID**: `01_project_scaffold`
> **目标**: 初始化 TempoOS 统一平台项目结构、配置系统、依赖管理、测试框架
> **依赖**: 无（第一个执行的 plan）
> **原则**: 框架 only，不写业务逻辑。所有后续 plan 必须从第一天起可测试。
> **预估**: 1 天

---

## 步骤

### 1. 创建项目根目录结构

在 `数字员工统一平台/` 下创建以下结构：

```
tempo_os/
├── kernel/             # 事件总线、时钟、调度器、注册中心
├── memory/             # FSM、Blackboard
├── protocols/          # TempoEvent schema、事件类型常量
├── nodes/              # 内置节点框架
│   ├── base.py         # BaseNode 抽象基类
│   └── biz/            # 业务节点（后续迁入）
├── resilience/         # 幂等、Fan-in、Hard Stop（后续迁入）
├── runtime/            # LLM Gateway、ToolRegistry、Webhook 调用器
├── api/                # FastAPI 路由层
│   ├── workflow.py
│   ├── registry_api.py
│   ├── state.py
│   ├── gateway.py
│   └── ws.py           # WebSocket 推送
├── storage/            # PG 持久层
├── core/               # Config、Tenant、Meta
├── __init__.py
└── main.py             # 应用入口（uvicorn 启动）
tests/
├── conftest.py         # 共享 fixtures
├── unit/
├── integration/
└── e2e/
config/
├── platform_config.yaml   # 平台配置模板
flows/                     # YAML 流程定义目录
├── examples/
```

确保所有子目录都有 `__init__.py`。

### 2. 配置系统

创建 `tempo_os/core/config.py`：
- 使用 Pydantic `BaseSettings` 从 `.env` 加载
- 必需字段：
  - `REDIS_URL` (default: `redis://localhost:6379/0`)
  - `DATABASE_URL` (default: `postgresql+asyncpg://tempo:password@localhost:5432/tempo_os`)
  - `DASHSCOPE_API_KEY`
  - `DASHSCOPE_MODEL` (default: `qwen-max`)
  - `LOG_LEVEL` (default: `INFO`)
  - `TEMPO_ENV` (default: `dev`)
  - `SESSION_TTL` (default: `1800`)
  - `MAX_RETRY` (default: `3`)
- 创建 `.env.example` 模板

### 3. 依赖管理

创建 `requirements.txt`：

```
# Core
pydantic>=2.0
pydantic-settings>=2.0
redis[hiredis]>=5.0
asyncpg>=0.29
sqlalchemy[asyncio]>=2.0
alembic>=1.13
pgvector>=0.3
pyyaml>=6.0

# LLM
dashscope>=1.17
httpx>=0.27

# API
fastapi>=0.110
uvicorn[standard]>=0.27
websockets>=12.0
python-jose[cryptography]>=3.3   # JWT

# Test
pytest>=8.0
pytest-asyncio>=0.23
pytest-cov>=4.1
fakeredis[lua]>=2.21
httpx>=0.27                       # AsyncClient for API tests
```

创建 `pyproject.toml` 包含项目元信息。

### 4. 测试框架

创建 `tests/conftest.py`：
- `mock_redis` fixture：FakeRedis 实例（async）
- `mock_tenant_id` fixture：返回 `"test_tenant"`
- `mock_session_id` fixture：返回 UUID
- `db_session` fixture：占位（Plan 03 实现）

创建 `pytest.ini`：
```ini
[pytest]
asyncio_mode = auto
testpaths = tests
addopts = -v --cov=tempo_os --cov-report=term-missing
```

### 5. 应用入口占位

创建 `tempo_os/main.py`：
- FastAPI app 实例（lifespan 管理 Redis/PG 连接）
- `/health` 端点返回 `{"status": "ok", "version": "0.1.0"}`
- 后续 plan 会向 app 注册 router

### 6. 验收

- [ ] `pip install -r requirements.txt` 成功
- [ ] `python -c "from tempo_os.core.config import settings; print(settings)"` 无报错
- [ ] `pytest` 运行成功（0 tests collected is OK）
- [ ] `uvicorn tempo_os.main:app --port 8000` 启动后 `/health` 返回 200
```
