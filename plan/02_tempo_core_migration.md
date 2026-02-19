# Plan 02：从 tempo-core 迁入内核代码
> **Task ID**: `02_tempo_core_migration`
> **目标**: 将 `数字员工/tempo-core/tempo/` 中的核心组件迁入本项目，作为平台 OS 内核
> **依赖**: `01_project_scaffold`
> **源代码**: `d:\项目\数字员工\tempo-core\tempo\`
> **预估**: 1 天

---

## 迁入范围

### 必须迁入（整体复制 + 适配 import 路径）

| 源路径 | 目标路径 | 说明 |
|--------|---------|------|
| `tempo/kernel/bus.py` | `tempo_os/kernel/bus.py` | RedisBus（Pub/Sub + Stream） |
| `tempo/kernel/clock.py` | `tempo_os/kernel/clock.py` | TempoClock（心跳+超时） |
| `tempo/kernel/dispatcher.py` | `tempo_os/kernel/dispatcher.py` | KernelDispatcher（后续 Plan 04 增强） |
| `tempo/kernel/registry.py` | `tempo_os/kernel/registry.py` | WorkerRegistry → 后续改为 NodeRegistry |
| `tempo/kernel/namespace.py` | `tempo_os/kernel/namespace.py` | 租户隔离 key 生成 |
| `tempo/kernel/redis_client.py` | `tempo_os/kernel/redis_client.py` | Redis 连接池 |
| `tempo/kernel/tick_logger.py` | `tempo_os/kernel/tick_logger.py` | Tick 日志 |
| `tempo/memory/fsm.py` | `tempo_os/memory/fsm.py` | TempoFSM |
| `tempo/memory/blackboard.py` | `tempo_os/memory/blackboard.py` | TenantBlackboard |
| `tempo/protocols/schema.py` | `tempo_os/protocols/schema.py` | TempoEvent |
| `tempo/protocols/events.py` | `tempo_os/protocols/events.py` | 事件类型常量 |
| `tempo/core/config.py` | 合并到 `tempo_os/core/config.py` | 合并配置字段 |
| `tempo/core/tenant.py` | `tempo_os/core/tenant.py` | TenantContext |
| `tempo/core/meta.py` | `tempo_os/core/meta.py` | AgentDef |
| `tempo/runtime/base_llm.py` | `tempo_os/runtime/base_llm.py` | LLM 基类 |
| `tempo/runtime/llm_gateway.py` | `tempo_os/runtime/llm_gateway.py` | LLM Gateway |
| `tempo/runtime/tools.py` | `tempo_os/runtime/tools.py` | ToolRegistry |
| `tempo/runtime/sandbox.py` | `tempo_os/runtime/sandbox.py` | 代码沙箱 |
| `tempo/workers/base.py` | `tempo_os/workers/base.py` | BaseWorker（保留兼容，新节点用 BaseNode） |

### 不迁入

| 源路径 | 原因 |
|--------|------|
| `tempo/api/server.py` | 将在 Plan 07 重写，当前版本太简单 |
| `tempo/demo/gradio_app.py` | Demo 专用，不进平台 |
| `tempo/prompts/*` | 角色 prompt，后续按需 |

### 迁入测试

| 源路径 | 目标路径 |
|--------|---------|
| `tempo-core/tests/conftest.py` | 合并到 `tests/conftest.py` |
| `tempo-core/tests/unit/test_bus.py` | `tests/unit/test_bus.py` |
| `tempo-core/tests/unit/test_blackboard.py` | `tests/unit/test_blackboard.py` |
| `tempo-core/tests/unit/test_fsm.py` | `tests/unit/test_fsm.py` |
| `tempo-core/tests/unit/test_schema.py` | `tests/unit/test_schema.py` |
| `tempo-core/tests/unit/test_clock.py` | `tests/unit/test_clock.py` |
| `tempo-core/tests/unit/test_worker.py` | `tests/unit/test_worker.py` |
| `tempo-core/tests/unit/test_registry.py` | `tests/unit/test_registry.py` |
| `tempo-core/tests/scenarios/*` | `tests/scenarios/` |

---

## 步骤

### 1. 批量复制源文件
- 按上表复制文件到目标路径

### 2. 全局替换 import 路径
- `from tempo.` → `from tempo_os.`
- `import tempo.` → `import tempo_os.`
- 确认所有 `__init__.py` 正确 re-export

### 3. 合并配置
- 将 `tempo/core/config.py` 的 `TempoSettings` 字段合并到 Plan 01 创建的 `tempo_os/core/config.py`
- 保持 Pydantic BaseSettings，新增字段而非重复

### 4. 运行测试
- `pytest tests/unit/` 全部通过
- `pytest tests/scenarios/` 全部通过

### 5. 验收

- [ ] `from tempo_os.kernel.bus import RedisBus` 无报错
- [ ] `from tempo_os.memory.fsm import TempoFSM` 无报错
- [ ] `from tempo_os.protocols.schema import TempoEvent` 无报错
- [ ] `from tempo_os.memory.blackboard import TenantBlackboard` 无报错
- [ ] 原 tempo-core 的单元/场景测试在新路径下全部通过（至少覆盖 bus/blackboard/fsm/schema/dispatcher/worker/registry）
```
