# Plan 07：FastAPI 网关（系统调用层）
> **Task ID**: `07_api_gateway`
> **目标**: 实现完整的 HTTP API + WebSocket 推送，作为平台对外的唯一入口
> **依赖**: `04_engine_enhance`, `06_node_framework`
> **预估**: 3 天

---

## 步骤

### 1. 通用基础设施

**`tempo_os/api/deps.py`** — 依赖注入：
```python
async def get_current_tenant(token: str = Header(...)) -> TenantContext:
    """从 JWT/API Token 解析 tenant_id、user_id、roles"""
    # Phase 1 可先用简单 API Key → tenant_id 映射
    # 后续替换为 JWT 验证

async def get_session_manager() -> SessionManager
async def get_node_registry() -> NodeRegistry
async def get_blackboard(tenant: TenantContext) -> TenantBlackboard
```

**`tempo_os/api/errors.py`** — 统一错误结构：
```python
class APIError(Exception):
    code: str       # "SESSION_NOT_FOUND" / "INVALID_TRANSITION" / ...
    message: str
    trace_id: str
    details: dict

@app.exception_handler(APIError)
async def handle_api_error(request, exc):
    return JSONResponse(status_code=..., content={
        "code": exc.code, "message": exc.message,
        "trace_id": exc.trace_id, "details": exc.details
    })
```

**`tempo_os/api/middleware.py`** — 中间件：
- `TraceMiddleware`：为每个请求生成/透传 `trace_id`（`X-Trace-Id` header）
- `IdempotencyMiddleware`：检查 `Idempotency-Key` header，防止重复提交

### 2. Workflow API

创建 `tempo_os/api/workflow.py`：

```
POST   /api/workflow/start
  Body: { flow_id?, node_id?, params, inherit_session? }
  → 创建 Session（显式流程或隐式会话）→ 返回 { session_id, state }

POST   /api/workflow/{session_id}/event
  Body: { event_type, payload? }
  → 推进流程 → 返回 { new_state, ui_schema?, artifacts? }

GET    /api/workflow/{session_id}/state
  → 返回当前 Session 状态 + FSM state + 可用事件列表

DELETE /api/workflow/{session_id}
  → 终止流程 (Hard Stop)

POST   /api/workflow/{session_id}/callback
  Body: { step, result }  （外部 Webhook 回调）
  → 处理回调 → 推进流程
```

### 3. Registry API

创建 `tempo_os/api/registry_api.py`：

```
GET    /api/registry/nodes
  → 返回所有已注册节点列表（builtin + webhook）

POST   /api/registry/nodes
  Body: { node_id, endpoint, param_schema, name, description }
  → 注册外部 Webhook 节点

GET    /api/registry/flows
  → 返回所有已注册流程列表

POST   /api/registry/flows
  Body: { flow_id, name, yaml_content }
  → 注册/更新流程 YAML（含校验）

GET    /api/registry/flows/{flow_id}
  → 返回流程详情（YAML + 状态图）
```

### 4. State API

创建 `tempo_os/api/state.py`：

```
GET    /api/state/{session_id}
  → 返回该 Session 的全部 Blackboard 状态

GET    /api/state/{session_id}/{key}
  → 返回指定 key 的值

PUT    /api/state/{session_id}/{key}
  Body: { value }
  → 写入（限制：仅调试/管理用途）
```

### 5. Model Gateway API

创建 `tempo_os/api/gateway.py`：

```
POST   /api/llm/chat
  Body: { messages, model?, temperature?, stream? }
  → 调用 DashScope → 返回结果（支持 SSE streaming）

POST   /api/llm/embedding
  Body: { texts }
  → 调用 DashScope embedding → 返回向量
```

### 6. WebSocket 事件推送

创建 `tempo_os/api/ws.py`：

```
WS     /ws/events/{session_id}
  → 鉴权（从 query param 或首条消息获取 token）
  → 订阅该 session 的 Redis Bus 事件
  → 实时推送 TempoEvent JSON 到前端
  → 同时接受前端发来的 Event（双向）
  → 断开时清理 subscription
```

**关键实现**：
- 每个 WS 连接创建一个 RedisBus 订阅
- 过滤只推送该 session_id 的事件
- 连接断开时确保 pubsub cleanup（避免泄漏）
- 心跳 ping/pong 保活

### 7. 路由注册

在 `tempo_os/main.py` 中：
```python
app.include_router(workflow_router, prefix="/api")
app.include_router(registry_router, prefix="/api")
app.include_router(state_router, prefix="/api")
app.include_router(gateway_router, prefix="/api")
# WS 单独挂载
```

### 8. 测试

- `tests/unit/test_api_workflow.py`：start/event/state/delete
- `tests/unit/test_api_registry.py`：node/flow 注册与查询
- `tests/unit/test_api_state.py`：Blackboard 读写
- `tests/integration/test_api_e2e.py`：API 调用完整流程
- `tests/integration/test_ws.py`：WS 连接 + 事件推送

### 9. 验收

- [ ] `POST /api/workflow/start` 可启动流程
- [ ] `POST /api/workflow/{id}/event` 可推进流程
- [ ] `WS /ws/events/{id}` 可实时收到状态变更
- [ ] `POST /api/registry/flows` 可注册 YAML 流程
- [ ] `POST /api/llm/chat` 可调用 DashScope
- [ ] 所有 API 返回统一错误结构
- [ ] trace_id 从 HTTP → TempoEvent → PG 贯通
