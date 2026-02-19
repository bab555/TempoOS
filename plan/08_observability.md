# Plan 08：最小可观测性
> **Task ID**: `08_observability`
> **目标**: 实现 trace_id 全链路贯通、结构化日志、关键运行指标，使平台可运维
> **依赖**: `07_api_gateway`
> **预估**: 1 天

---

## 步骤

### 1. trace_id 全链路贯通

确保 `trace_id` 从请求入口到最终存储全程透传：

```
HTTP Request (X-Trace-Id header)
  → TraceMiddleware 生成/透传 trace_id
    → TempoEvent.trace_id 字段
      → KernelDispatcher 在推进时携带
        → workflow_events 表的 trace_id 字段
          → 前端 WS 推送时包含
```

如果请求没有 `X-Trace-Id`，中间件自动生成 UUID。

### 2. 结构化日志

创建 `tempo_os/core/logging.py`：
- 统一日志格式：`JSON` 结构（时间、级别、模块、trace_id、tenant_id、session_id、message）
- 关键组件自动附加上下文：
  - Dispatcher：`[dispatch] session={sid} state={from}→{to} node={ref}`
  - Node：`[node:{id}] session={sid} status={status} elapsed={ms}ms`
  - API：`[api] method={m} path={p} status={code} elapsed={ms}ms`

### 3. 关键指标收集

创建 `tempo_os/core/metrics.py`：
- 使用内存计数器（Phase 1 不引入 Prometheus，保持简单）
- 关键指标：
  - `sessions_active`：当前活跃 session 数
  - `sessions_total`：累计创建 session 数
  - `events_processed`：已处理事件数
  - `node_executions`：按 node_id 分组的执行次数/耗时/错误数
  - `bus_events_published`：Bus 发布事件数

暴露指标端点：
```
GET /api/metrics → { sessions_active: 3, events_processed: 1024, ... }
```

### 4. 事件回放能力

确认 `EventRepository.replay(session_id)` 可用：
- 按 `created_at` 排序返回该 session 的全部事件
- 用于故障排查："这个 session 经历了哪些状态变更？"

新增 API 端点：
```
GET /api/workflow/{session_id}/events → list of events (审计日志)
```

### 5. 健康检查增强

改造 `/health` 端点：
```json
{
  "status": "ok",
  "version": "0.1.0",
  "redis": "connected",
  "postgres": "connected",
  "nodes_registered": 5,
  "flows_registered": 2,
  "sessions_active": 3
}
```

### 6. 测试

- `tests/unit/test_trace_id.py`：trace_id 从 HTTP 到 Event 到 PG 贯通
- `tests/unit/test_metrics.py`：计数器正确递增

### 7. 验收

- [ ] 每个 API 请求日志包含 trace_id
- [ ] 每个 TempoEvent 包含 trace_id
- [ ] PG workflow_events 可按 trace_id 查询
- [ ] `/api/metrics` 返回当前运行指标
- [ ] `/api/workflow/{id}/events` 返回事件审计日志
- [ ] `/health` 显示所有组件状态

---

**Phase 1 到此完成。** 此时你拥有一个可独立运行的平台 OS：可注册节点/流程，通过 API 启动/推进工作流，WS 实时推送，PG 审计日志，基本可观测。
