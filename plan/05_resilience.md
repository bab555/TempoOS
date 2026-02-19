# Plan 05：可靠性特性迁入（从 Tempo Kernel 抽取）
> **Task ID**: `05_resilience`
> **目标**: 从 AISOP/tempo_kernel 迁入幂等恢复、Fan-in 并行汇合、Hard Stop 能力
> **依赖**: `04_engine_enhance`
> **源代码**: `d:\项目\AISOP\tempo_kernel\tempo_kernel\runtime\`
> **预估**: 2 天
> **注意**: 只迁"思想+核心逻辑"，不搬 tick-plan 执行模型

---

## 步骤

### 1. 幂等恢复（idempotency）

创建 `tempo_os/resilience/idempotency.py`：

**参考源**: `tempo_kernel/runtime/worker.py` 的 `_execute_action()` 中的幂等检查逻辑

**实现**:
```python
class IdempotencyGuard:
    """节点执行前检查、执行后记录，确保 at-least-once + 幂等"""

    async def before_execute(session_id, step, attempt) -> bool:
        """检查是否已执行过。返回 True=可执行，False=已完成跳过"""

    async def after_execute(session_id, step, attempt, status, result_hash):
        """记录执行结果"""

    async def should_retry(session_id, step) -> (bool, int):
        """检查是否应重试。返回 (should_retry, next_attempt)"""
```

- 存储层：使用 Plan 03 的 `IdempotencyRepository`（PG）
- 集成点：在 `KernelDispatcher.dispatch()` 调用节点前/后包一层 Guard

### 2. Fan-in 并行汇合

创建 `tempo_os/resilience/fan_in.py`：

**参考源**: `tempo_kernel/runtime/scheduler.py` 的 `deps_satisfied()` 逻辑

**适配改造**:
- 原版检查 SQLite `executed_actions` → 改为检查 PG `workflow_events`（某 session 的某 step 是否已有 STEP_DONE 事件）
- 原版检查 Redis Stream 跨节点结果 → 改为检查 Blackboard artifact 是否存在

```python
class FanInChecker:
    """并行节点汇合：只有当所有前置节点都完成时，才放行后续节点"""

    async def all_deps_done(session_id, required_steps: list[str]) -> bool:
        """检查所有依赖步骤是否已完成"""

    async def get_pending_deps(session_id, required_steps) -> list[str]:
        """返回尚未完成的依赖列表（调试用）"""
```

- 在 FSM YAML 中支持 `fan_in_deps` 字段：
  ```yaml
  transitions:
    - { from: [step_a_done, step_b_done], event: ALL_DONE, to: merge_step, fan_in: true }
  ```

### 3. Hard Stop 紧急终止

创建 `tempo_os/resilience/stopper.py`：

**参考源**: `tempo_kernel/runtime/stopper.py`

**适配改造**:
- 原版通过 `abort_latch` + kill 进程 → 改为：
  1. 在 Redis 设置 `abort:{session_id}` 标记
  2. 在 Blackboard 设置 `signal:abort = true`
  3. 发布 `ABORT` 事件到 Bus
  4. Dispatcher 检测到 abort 后跳过后续节点执行
  5. 正在执行的 builtin 节点通过 `blackboard.get_signal("abort")` 检查并自行终止
  6. 正在执行的 webhook 无法强杀，但回调时 Dispatcher 忽略结果

```python
class HardStopper:
    async def abort(session_id, reason, trace_id):
        """立即终止：设标记 + 发事件 + 写审计"""

    async def is_aborted(session_id) -> bool:
        """节点执行前/中检查"""
```

### 4. 重试策略

创建 `tempo_os/resilience/retry.py`：

```python
class RetryPolicy:
    max_attempts: int = 3
    backoff_base: float = 1.0        # 秒
    backoff_multiplier: float = 2.0  # 指数退避
    max_backoff: float = 60.0

    def next_delay(self, attempt: int) -> float:
        """计算下次重试延迟"""

class RetryManager:
    async def handle_node_error(session_id, step, error, policy):
        """错误处理：重试/死信/人工介入"""
```

### 5. 集成到 Dispatcher

修改 `KernelDispatcher.dispatch()`：
```
1. IdempotencyGuard.before_execute() → 如果已执行过则跳过
2. HardStopper.is_aborted() → 如果已 abort 则跳过
3. FanInChecker.all_deps_done() → 如果依赖未满足则等待
4. 执行节点
5. IdempotencyGuard.after_execute() → 记录结果
6. 如果失败 → RetryManager.handle_node_error()
```

### 6. 测试

- `tests/unit/test_idempotency.py`：重复执行检测、重试计数
- `tests/unit/test_fan_in.py`：2路/3路并行汇合
- `tests/unit/test_stopper.py`：abort 标记设置与检测
- `tests/integration/test_resilience_flow.py`：模拟节点失败→重试→成功/死信

### 7. 验收

- [ ] 同一步骤重复调用不会实际执行两次
- [ ] Fan-in 等待多个并行节点全部完成后才继续
- [ ] Hard Stop 可立即终止流程，后续节点不再执行
- [ ] 节点失败后按策略重试，超限进入 error 状态
