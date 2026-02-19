# Plan 16：A2UI 协议（后端输出）与合约测试（不做前端）
> **Task ID**: `16_a2ui_protocol`
> **目标**: 定义 `ui_schema` 标准协议（平台输出契约），提供 JSON Schema/示例库/校验器，并建立 API+WS 的"可交互验收"测试用例（不实现前端渲染）
> **依赖**: `07_api_gateway`
> **预估**: 2 天
> **交付物**: 协议、示例、校验、合约测试

---

## 步骤

### 1. UISchema 协议定义

创建 `tempo_os/protocols/ui_schema.py`：

```python
class UIComponent(BaseModel):
    type: str           # 组件类型
    props: dict         # 组件属性
    key: str = None     # 唯一标识（可选）

class UISchema(BaseModel):
    components: list[UIComponent]
    layout: str = "vertical"       # vertical | horizontal | grid
    title: str = None
    session_id: str = None
    step: str = None
```

标准组件类型（12 种）：
| type | 用途 | 核心 props |
|------|------|-----------|
| `kpi_card` | KPI 指标卡 | label, value, unit, trend |
| `table` | 数据表格 | columns, data, pagination |
| `bar_chart` | 柱状图 | x_field, y_field, data |
| `line_chart` | 折线图 | x_field, y_field, series |
| `pie_chart` | 饼图 | label_field, value_field, data |
| `form` | 输入表单 | fields: [{name, type, label, required}] |
| `action_buttons` | 操作按钮组 | actions: [{label, event, params}] |
| `markdown` | Markdown 内容 | content |
| `file_preview` | 文件预览 | url, type, name |
| `progress` | 流程进度 | steps: [{name, status}], current |
| `chat_message` | 对话消息 | role, content, timestamp |
| `selection_list` | 选择列表 | items: [{id, label, description}], multi |

### 2. 协议落点：ui_schema 如何进入事件流（平台必须固定）

统一规定：**节点执行完成后，平台对前端推送的事件 payload 必须包含 `ui_schema` 字段**。

建议承载位置（二选一，二者可同时存在但要固定字段名）：
- **A）在 `EVENT_RESULT` 的 `payload.ui_schema`**（推荐）：WS 订阅只要看事件就能渲染
- **B）在 `STATE_TRANSITION` 的 `payload.ui_schema`**：表示"进入某状态应该展示的 UI"

无论 A/B，字段名固定为：`ui_schema`，且内容必须可被 `UISchema` 模型校验通过。

### 3. JSON Schema（合约）与示例库

新增目录 `contracts/`：
- `contracts/ui_schema.schema.json`：UISchema JSON Schema（用于前后端一致校验）
- `contracts/examples/`：每种组件至少 1 个示例 JSON

平台侧要求：
- 节点返回的 `NodeResult.ui_schema` 必须通过 schema 校验
- WS 推送到前端的 `payload.ui_schema` 必须通过 schema 校验

### 4. 合约校验器（后端自测）

在 `tempo_os/protocols/ui_schema.py` 中提供 `validate_ui_schema(data: dict) -> UISchema`，并在：
- Dispatcher 发送事件前进行校验（不通过则降级为 `markdown` 错误块，避免把脏数据发给前端）
- 单元测试中对示例库做全量校验

### 5. 测试

- `tests/unit/test_ui_schema_contract.py`：
  - 加载 `contracts/examples/*.json`，逐个通过 `validate_ui_schema`
  - 针对非法字段做反例测试
- `tests/integration/test_ws_ui_schema.py`：
  - 启动一个最小 echo_flow
  - 建立 WS 订阅
  - 推进事件
  - 断言收到的事件中包含 `payload.ui_schema` 且可通过校验

### 6. 验收

- [ ] `contracts/ui_schema.schema.json` 完整覆盖 12 种组件
- [ ] 示例库全量通过校验
- [ ] WS 推送事件中固定包含 `payload.ui_schema`（或按约定位置）
- [ ] 至少 1 个端到端流程用例可验证：WS 收到 ui_schema、HTTP 可推进事件
