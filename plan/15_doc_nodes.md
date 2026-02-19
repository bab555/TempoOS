# Plan 15：文档助手节点迁入
> **Task ID**: `15_doc_nodes`
> **目标**: 从玄枢迁入 doc_plan / doc_write / doc_export 三个节点
> **依赖**: `06_node_framework`, `12_model_gateway`
> **源代码**: `d:\项目\玄枢（内部文档）\`（玄枢项目，LangGraph 工作流）
> **预估**: 2 天

---

## 迁移原则

1. **拆解玄枢的 LangGraph 工作流为独立节点**：每个 Skill 变成一个节点
2. **LLM 调用走平台 DashScope**
3. **导出能力（Playwright + Pandoc → DOCX）作为独立节点**

---

## 步骤

### 1. builtin://doc_plan 节点

创建 `tempo_os/nodes/biz/doc_plan.py`：

- 输入：`params = { topic, requirements, doc_type }`
- 执行：调 LLM 生成文档大纲（章节结构 + 每章要点）
- 输出：`artifacts = {"doc_outline": outline}`
- ui_schema：树形大纲展示 + 确认/修改按钮

### 2. builtin://doc_write 节点

创建 `tempo_os/nodes/biz/doc_write.py`：

- 输入：`params = { section_id? }`（写全文或指定章节）
- 执行：
  1. 从 Blackboard 读 `doc_outline`
  2. 逐章调 LLM 撰写内容（可并行，依赖 Fan-in）
  3. 合并为完整 Markdown
- 输出：`artifacts = {"doc_content": markdown_text}`
- ui_schema：Markdown 预览

### 3. builtin://doc_export 节点

创建 `tempo_os/nodes/biz/doc_export.py`：

- 输入：`params = { format: "docx" | "pdf" }`
- 执行：
  1. 从 Blackboard 读 `doc_content`
  2. Markdown → DOCX（python-docx 或 Pandoc）
  3. 可选：Playwright 渲染 HTML → PDF
- 输出：`artifacts = {"document_url": "/files/xxx.docx"}`
- ui_schema：文件预览 + 下载按钮

### 4. 文档生成流程 YAML

创建 `flows/examples/doc_gen_flow.yaml`：
```yaml
name: doc_gen_flow
description: "对话式文档生成：规划→撰写→导出"
states: [planning, plan_done, writing, write_done, exporting, end]
initial_state: planning
state_node_map:
  planning:  builtin://doc_plan
  writing:   builtin://doc_write
  exporting: builtin://doc_export
user_input_states: [plan_done, write_done]
transitions:
  - { from: planning,   event: STEP_DONE,    to: plan_done }
  - { from: plan_done,  event: USER_CONFIRM, to: writing }
  - { from: plan_done,  event: USER_MODIFY,  to: planning }
  - { from: writing,    event: STEP_DONE,    to: write_done }
  - { from: write_done, event: USER_CONFIRM, to: exporting }
  - { from: write_done, event: USER_MODIFY,  to: writing }
  - { from: exporting,  event: STEP_DONE,    to: end }
```

### 5. 依赖管理

```
python-docx>=1.1
```

### 6. 测试

- `tests/unit/test_doc_plan_node.py`：大纲生成
- `tests/unit/test_doc_write_node.py`：内容撰写
- `tests/unit/test_doc_export_node.py`：DOCX 导出
- `tests/e2e/test_doc_flow.py`：完整文档生成流程

### 7. 验收

- [ ] doc_plan 节点生成结构化大纲
- [ ] doc_write 节点按大纲撰写完整文档
- [ ] doc_export 节点导出 DOCX 文件
- [ ] 数据通过 Blackboard 在三个节点间正确流转
- [ ] doc_gen_flow 完整流程可运行
