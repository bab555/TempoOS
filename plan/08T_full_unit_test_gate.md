# Plan 08T：全量单元测试闸门（框架先全绿，再接业务节点）
> **Task ID**: `08T_full_unit_test_gate`
> **目标**: 在"纯框架"完成后，强制跑完全量单元测试与关键集成测试，确保框架问题不会影响独立 worker/node
> **依赖**: `08_observability`
> **预估**: 0.5 天
> **定位**: 这是一个 **Quality Gate**，不是新功能开发；没有通过则禁止进入 Phase 2/3。

---

## 核心原则

1. **框架与业务隔离**：框架测试必须在"不引入任何业务节点"的情况下可全绿。
2. **全量单元测试是红线**：任何框架改动合入前都必须全绿。
3. **关键路径集成测试兜底**：至少覆盖 start → dispatch → node → event → state → ws 的最小闭环。

---

## 步骤

### 1. 固化测试分层与执行命令

约定测试目录：

- `tests/unit/`：纯单元测试（不依赖外部 Redis/PG 实例，使用 FakeRedis/测试 DB）
- `tests/integration/`：集成测试（可使用 docker-compose 的 Redis/PG，或本机服务）
- `tests/e2e/`：端到端测试（最小流程闭环，覆盖 WS）

约定执行命令（示例）：

```bash
# 1) 纯单元测试（必须全绿）
pytest -q tests/unit

# 2) 框架关键集成（必须全绿）
pytest -q tests/integration -k "engine or api or ws or storage"

# 3) 覆盖率门槛（建议）
pytest -q --cov=tempo_os --cov-report=term-missing --cov-fail-under=85
```

> 说明：覆盖率阈值可先从 75% 起步，框架稳定后提升到 85%+。

### 2. 增加"框架隔离"回归用例（防止影响独立 worker）

新增/确保以下用例存在并在 unit/integration 中全绿：

- **Kernel/Bus/FSM/Blackboard 不依赖业务节点**：导入、初始化、基本操作可用
- **Dispatcher 在仅注册 echo 节点时可跑通**：start → dispatch → result → transition
- **Node 框架接口稳定性**：
  - BaseNode.execute 的签名与 NodeResult 字段完整性
  - NodeRegistry 注册/解析 `builtin://` 与 `http://`
- **API 合约稳定性**：
  - `/api/workflow/start`、`/api/workflow/{id}/event`、`/api/workflow/{id}/state`
  - `WS /ws/events/{id}` 能收到事件
- **审计与可回放**：`workflow_events` 能完整记录，`/api/workflow/{id}/events` 可读取

### 3. 建立 CI（可选但推荐）

在 CI（GitHub Actions / GitLab CI / Jenkins）中建立两条流水线：

- **PR/merge gate**：只跑 `tests/unit` + 覆盖率门槛
- **nightly**：跑 `tests/unit + integration + e2e`

### 4. 失败策略

当以下任一项失败，必须阻断后续业务节点迁入：
- 任意 unit test 失败
- 任意关键 integration test 失败（engine/api/ws/storage）
- 覆盖率低于阈值（若启用）

---

## 验收

- [ ] `pytest tests/unit` 全绿
- [ ] `pytest tests/integration -k "engine or api or ws or storage"` 全绿
- [ ] 覆盖率达到设定阈值（若启用）
- [ ] 在"无业务节点"的最小环境下（仅 echo 节点 + 示例 flow）可跑通 API+WS 闭环
