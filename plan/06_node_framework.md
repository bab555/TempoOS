# Plan 06：内置节点框架
> **Task ID**: `06_node_framework`
> **目标**: 建立 BaseNode 抽象基类、NodeResult 数据结构、NodeRegistry、以及第一批基础节点
> **依赖**: `04_engine_enhance`
> **预估**: 2 天

---

## 步骤

### 1. BaseNode 抽象基类

创建 `tempo_os/nodes/base.py`：

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class NodeResult:
    status: str                      # "success" | "error" | "need_user_input"
    result: dict                     # 业务结果
    ui_schema: Optional[dict] = None # A2UI 渲染描述
    artifacts: dict = field(default_factory=dict)  # 写入 Blackboard 的产物
    next_events: list[str] = field(default_factory=list)  # 接受的下一步事件
    error_message: Optional[str] = None

class BaseNode(ABC):
    """所有内置节点的基类"""

    node_id: str           # 节点唯一标识（如 "llm_call"）
    name: str              # 显示名称
    description: str       # 描述
    param_schema: dict     # 输入参数 JSON Schema

    @abstractmethod
    async def execute(
        self,
        session_id: str,
        tenant_id: str,
        params: dict,
        blackboard: TenantBlackboard,
    ) -> NodeResult:
        ...
```

### 2. NodeRegistry（统一注册表）

创建 `tempo_os/kernel/node_registry.py`：

```python
class NodeRegistry:
    """统一管理内置节点和外部 Webhook 节点"""

    def register_builtin(self, node_id: str, node: BaseNode)
    def register_webhook(self, node_id: str, endpoint: str, param_schema: dict)
    def get(self, node_id: str) -> (BaseNode | WebhookInfo)
    def resolve_ref(self, node_ref: str) -> (BaseNode | WebhookInfo):
        """解析 builtin://xxx 或 http://xxx"""
    def list_all() -> list[NodeInfo]
```

- 启动时自动发现并注册所有内置节点（通过 `__init_subclass__` 或显式注册）
- 同步到 PG `registry_nodes` 表（Plan 03）

### 3. 第一批基础节点

**`tempo_os/nodes/echo.py`** — 测试节点：
- 输入什么返回什么，用于验证框架

**`tempo_os/nodes/conditional.py`** — 条件分支：
- 从 Blackboard 读取指定 key，按规则返回不同事件

**`tempo_os/nodes/transform.py`** — 数据变换：
- JSONPath 提取、模板渲染、格式转换

**`tempo_os/nodes/http_request.py`** — 通用 HTTP 调用：
- 发起 HTTP 请求到任意 URL，返回结果
- 区别于 webhook 节点：这是进程内发 HTTP，不是被动等回调

**`tempo_os/nodes/notification.py`** — 通知：
- 通过 Bus 发送通知事件（前端 WS 接收）

### 4. 节点自动注册

创建 `tempo_os/nodes/__init__.py`：

```python
from .echo import EchoNode
from .conditional import ConditionalNode
from .transform import TransformNode
from .http_request import HTTPRequestNode
from .notification import NotificationNode

BUILTIN_NODES = {
    "echo":          EchoNode(),
    "conditional":   ConditionalNode(),
    "transform":     TransformNode(),
    "http_request":  HTTPRequestNode(),
    "notification":  NotificationNode(),
}
```

在 `main.py` 启动时将 `BUILTIN_NODES` 注册到 `NodeRegistry`。

### 5. Webhook 调用器

创建 `tempo_os/runtime/webhook.py`：

```python
class WebhookCaller:
    async def call(endpoint, session_id, step, params, callback_url):
        """发送 HTTP POST 到外部 Webhook，携带回调 URL"""

    async def handle_callback(session_id, step, result):
        """处理外部 Webhook 的回调"""
```

### 6. 示例 YAML 流程

创建 `flows/examples/echo_flow.yaml`：
```yaml
name: echo_test_flow
states: [start, echoed, end]
initial_state: start
state_node_map:
  start: builtin://echo
transitions:
  - { from: start, event: STEP_DONE, to: echoed }
  - { from: echoed, event: USER_CONFIRM, to: end }
user_input_states: [echoed]
```

### 7. 测试

- `tests/unit/test_base_node.py`：BaseNode 接口合规测试
- `tests/unit/test_node_registry.py`：注册/查询/解析
- `tests/unit/test_echo_node.py`：echo 节点执行
- `tests/unit/test_conditional_node.py`：条件分支
- `tests/integration/test_echo_flow.py`：完整 echo 流程端到端

### 8. 验收

- [ ] 5 个基础节点可注册、可查询
- [ ] echo 节点可在流程中被 Dispatcher 调用
- [ ] conditional 节点可根据 Blackboard 数据做分支
- [ ] echo_flow YAML 可加载、启动、推进、完成
- [ ] NodeRegistry.list_all() 返回所有已注册节点（含 builtin + webhook）
