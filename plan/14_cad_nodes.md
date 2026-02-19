# Plan 14：CAD 业务节点迁入
> **Task ID**: `14_cad_nodes`
> **目标**: 从数字员工2迁入 cad_inspect / cad_modify 两个业务节点
> **依赖**: `06_node_framework`
> **源代码**: `d:\项目\数字员工2\src\worker\`
> **预估**: 2 天

---

## 迁移原则

1. **只迁域逻辑**（Inspector/Compiler/Sandbox），不迁 FastAPI 外壳和 Session 管理
2. **版本链由平台 Blackboard 管理**（不用 数字员工2 的内存 `EditSession`）
3. **LLM 调用统一走平台 DashScope**（替换 `worker/llm.py` 的 QwenClient）

---

## 步骤

### 1. CAD 底层能力迁入

从 `数字员工2/src/worker/` 复制以下文件到 `tempo_os/nodes/biz/cad/`：

| 源文件 | 目标 | 说明 |
|--------|------|------|
| `inspector.py` | `cad/inspector.py` | DXF Manifest 提取（依赖 ezdxf） |
| `compiler.py` | `cad/compiler.py` | 指令→Python 脚本编译（改用平台 DashScope） |
| `sandbox.py` | `cad/sandbox.py` | 脚本执行沙箱（CADContext） |
| `dxf_parser.py` | `cad/dxf_parser.py` | DXF 实体解析 |
| `converter.py` | `cad/converter.py` | DWG↔DXF 转换（ODA） |

适配 import 路径，替换 `from .llm import QwenClient` 为平台 DashScope。

### 2. builtin://cad_inspect 节点

创建 `tempo_os/nodes/biz/cad_inspect.py`：

```python
class CADInspectNode(BaseNode):
    node_id = "cad_inspect"
    name = "CAD 图纸解析"

    async def execute(self, session_id, tenant_id, params, blackboard):
        # params: { file_path: "..." }
        inspector = Inspector(params["file_path"])
        inspector.load()
        manifest = inspector.get_manifest()
        bbox = inspector.get_bbox()

        return NodeResult(
            status="success",
            result={"manifest": manifest, "bbox": bbox},
            artifacts={"cad_manifest": manifest, "cad_bbox": bbox},
            ui_schema=self._build_manifest_ui(manifest, bbox),
        )
```

### 3. builtin://cad_modify 节点

创建 `tempo_os/nodes/biz/cad_modify.py`：

```python
class CADModifyNode(BaseNode):
    node_id = "cad_modify"
    name = "CAD 图纸编辑"

    async def execute(self, session_id, tenant_id, params, blackboard):
        # params: { file_path, instruction, create_mode? }
        # 1. 从 Blackboard 读取 manifest（如果有前序 inspect）
        manifest = await blackboard.get_artifact("cad_manifest")

        # 2. 编译指令为 Python 脚本（通过平台 DashScope）
        compiler = Compiler(dashscope_client=self.dashscope)
        inspector = Inspector(params["file_path"])
        inspector.load()
        execution_report = compiler.compile_and_run(inspector, params["instruction"],
                                                     empty_area=params.get("empty_area"))

        # 3. 保存修改后的 DXF
        output_path = f"/tmp/{session_id}_modified.dxf"
        inspector.doc.saveas(output_path)

        # 4. 计算 diff
        diff = compute_diff(params["file_path"], output_path)

        return NodeResult(
            status="success",
            result={"output_path": output_path, "diff": diff, "execution_report": execution_report},
            artifacts={"cad_output": output_path, "cad_diff": diff},
            ui_schema=self._build_diff_ui(diff),
            next_events=["USER_CONFIRM", "USER_ROLLBACK"],
        )
```

### 4. CAD 编辑流程 YAML

创建 `flows/examples/cad_edit_flow.yaml`：
```yaml
name: cad_edit_flow
description: "CAD 图纸 AI 编辑流程"
states: [inspect, inspect_done, modify, modify_done, end]
initial_state: inspect
state_node_map:
  inspect: builtin://cad_inspect
  modify:  builtin://cad_modify
user_input_states: [inspect_done, modify_done]
transitions:
  - { from: inspect,      event: STEP_DONE,    to: inspect_done }
  - { from: inspect_done, event: USER_CONFIRM, to: modify }
  - { from: modify,       event: STEP_DONE,    to: modify_done }
  - { from: modify_done,  event: USER_CONFIRM, to: end }
  - { from: modify_done,  event: USER_ROLLBACK, to: modify }
```

### 5. 依赖管理

在 `requirements.txt` 新增：
```
ezdxf>=0.18
```

### 6. 测试

- `tests/unit/test_cad_inspect_node.py`：解析测试 DXF 文件
- `tests/unit/test_cad_modify_node.py`：Mock DashScope，验证编译+执行
- `tests/e2e/test_cad_flow.py`：完整 inspect→modify→confirm 流程

### 7. 验收

- [ ] cad_inspect 节点可解析 DXF 并返回 Manifest + ui_schema
- [ ] cad_modify 节点可编译指令为脚本并执行修改
- [ ] 修改结果通过 Blackboard 传递，前端可收到 diff
- [ ] cad_edit_flow 完整流程可运行
