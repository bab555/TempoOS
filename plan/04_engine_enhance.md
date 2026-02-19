# Plan 04：工作流引擎增强（核心中的核心）
> **Task ID**: `04_engine_enhance`
> **目标**: 在迁入的 tempo-core 基础上，实现双模 Dispatcher、原子 FSM 推进、隐式会话、Session 生命周期
> **依赖**: `02_tempo_core_migration`, `03_pg_storage`
> **预估**: 3 天
> **重要性**: 这是整个平台的"CPU"，后续所有节点执行都经过这里

---

## 步骤

### 1. FSM 原子推进

改造 `tempo_os/memory/fsm.py` 的 `advance()` 方法：

**当前问题**: `advance()` 先读后写两次 Redis 调用，多实例下有竞态。

**改造方案**（二选一）：
- **方案 A（推荐）**: Redis Lua 脚本实现 compare-and-set
  ```lua
  -- KEYS[1] = session state hash key
  -- ARGV[1] = expected current state, ARGV[2] = event_type, ARGV[3] = new_state
  local cur = redis.call('HGET', KEYS[1], '_fsm_state')
  if cur == ARGV[1] then
      redis.call('HSET', KEYS[1], '_fsm_state', ARGV[3])
      return ARGV[3]
  else
      return redis.error_reply('CONFLICT:' .. (cur or 'nil'))
  end
  ```
- **方案 B**: Redis 分布式锁（`session:{id}:lock`，TTL 5s）

新增 `TempoFSM.advance_atomic(session_id, event_type)` → 返回 new_state 或抛 `ConflictError`。

### 2. 双模 KernelDispatcher

改造 `tempo_os/kernel/dispatcher.py`：

**新增 `NodeRegistry` 查询**：
- 收到 Event → FSM advance → 查 `state_node_map` 获得 `node_ref`
- 根据 `node_ref` 前缀路由：
  - `builtin://xxx` → 从内存 `NODE_REGISTRY` 取 node 实例 → 调用 `node.execute()`
  - `http://xxx` 或 `https://xxx` → HTTP POST 到 webhook → 记录 pending，等回调

**新增事件审计**：
- 每次状态推进写入 PG `workflow_events`（调用 `EventRepository.append()`）
- 附带 `trace_id`（从原始 Event 透传）

**新增结果处理**：
- builtin 节点返回 `NodeResult` → 根据 `status` 发出不同事件：
  - `success` → `STEP_DONE`
  - `error` → `ERROR`（触发重试或进入 error 状态）
  - `need_user_input` → `NEED_USER_INPUT`（FSM 进入 waiting_user）

### 3. 隐式会话

新增 `tempo_os/kernel/session_manager.py`：

```python
class SessionManager:
    async def start_flow(tenant_id, flow_id, params) -> session_id:
        """显式流程：加载 YAML FSM，创建 Session"""

    async def start_single_node(tenant_id, node_id, params) -> session_id:
        """隐式会话：创建最小 FSM [execute] → STEP_DONE → [end]"""

    async def inherit_session(new_flow_id, from_session_id, from_step):
        """继承前一个 Session 的 Blackboard，从指定步骤开始"""

    async def push_event(session_id, event_type, payload):
        """推进流程（用户确认/取消/修改）"""
```

### 4. Session 生命周期

在 `SessionManager` 中实现：
- 状态集：`idle/running/waiting_user/paused/completed/error`
- TTL 检查：`TempoClock` 定期扫描 → 超时 session 转 `paused`
- 控制指令：`PAUSE/RESUME/ABORT/RETRY` 作为一等事件

### 5. Flow YAML 加载

创建 `tempo_os/kernel/flow_loader.py`：
- 从 PG `workflow_flows` 或本地 `flows/` 目录加载 YAML
- 解析 `state_node_map`（状态→节点映射）
- 解析 `user_input_states`（哪些状态需要用户确认）
- 校验：所有引用的节点必须已注册

### 6. 测试

- `tests/unit/test_fsm_atomic.py`：并发推进测试（模拟多实例竞争）
- `tests/unit/test_dispatcher_dual.py`：builtin 节点调用 + webhook mock
- `tests/unit/test_session_manager.py`：显式/隐式会话创建、继承、TTL
- `tests/integration/test_engine_flow.py`：完整流程 idle→executing→waiting→executing→completed

### 7. 验收

- [ ] FSM advance 在并发下不会重复推进（Lua CAS 或锁生效）
- [ ] builtin 节点可被 Dispatcher 进程内调用
- [ ] http webhook mock 可被 Dispatcher 发送请求
- [ ] 隐式会话可创建、执行单步、保持 alive
- [ ] 显式流程可从 YAML 加载、逐步推进、等待用户输入
- [ ] 每次推进在 PG workflow_events 有审计记录
