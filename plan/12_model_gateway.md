# Plan 12：模型网关节点
> **Task ID**: `12_model_gateway`
> **目标**: 实现统一的 LLM 调用节点，封装 DashScope，支持 chat/extract/embedding/streaming/tool calling
> **依赖**: `06_node_framework`
> **预估**: 2 天

---

## 步骤

### 1. DashScope 封装

创建 `tempo_os/runtime/dashscope_wrapper.py`：

```python
class DashScopeClient:
    """统一的 DashScope 调用封装"""

    async def chat(messages, model, temperature, stream) -> str | AsyncIterator[str]
    async def chat_with_tools(messages, tools, model) -> (content, tool_calls)
    async def extract(text, schema, model) -> dict
    async def embedding(texts, model) -> list[list[float]]
```

- 错误重试（指数退避，最多 3 次）
- Token 计量（记录 input/output tokens）
- 超时控制

### 2. builtin://llm_call 节点

创建 `tempo_os/nodes/llm_call.py`：

```python
class LLMCallNode(BaseNode):
    node_id = "llm_call"
    name = "LLM 对话"

    async def execute(self, session_id, tenant_id, params, blackboard):
        # params: { messages, model?, temperature?, system_prompt? }
        result = await self.dashscope.chat(
            messages=params["messages"],
            model=params.get("model", "qwen-max"),
        )
        return NodeResult(
            status="success",
            result={"content": result},
            artifacts={"llm_response": result},
            ui_schema={"components": [
                {"type": "chat_message", "props": {"role": "assistant", "content": result}}
            ]},
        )
```

### 3. builtin://llm_extract 节点

创建 `tempo_os/nodes/llm_extract.py`：

```python
class LLMExtractNode(BaseNode):
    node_id = "llm_extract"
    name = "LLM 结构化抽取"

    async def execute(self, session_id, tenant_id, params, blackboard):
        # params: { text, output_schema: {...} }
        # 使用 function calling 或 JSON mode 抽取结构化数据
        extracted = await self.dashscope.extract(
            text=params["text"],
            schema=params["output_schema"],
        )
        return NodeResult(
            status="success",
            result=extracted,
            artifacts={"extracted_data": extracted},
        )
```

### 4. builtin://embedding 节点

创建 `tempo_os/nodes/embedding.py`：

```python
class EmbeddingNode(BaseNode):
    node_id = "embedding"
    name = "文本向量化"

    async def execute(self, session_id, tenant_id, params, blackboard):
        vectors = await self.dashscope.embedding(params["texts"])
        return NodeResult(
            status="success",
            result={"vectors": vectors, "dim": len(vectors[0])},
            artifacts={"embeddings": vectors},
        )
```

### 5. builtin://ui_schema_gen 节点

创建 `tempo_os/nodes/ui_schema_gen.py`：

```python
class UISchemaGenNode(BaseNode):
    node_id = "ui_schema_gen"
    name = "UISchema 生成"

    async def execute(self, session_id, tenant_id, params, blackboard):
        # 让 LLM 根据数据自动生成 Dashboard 配置
        data = params.get("data") or await blackboard.get_artifact(params["artifact_key"])
        ui_schema = await self.dashscope.chat(
            messages=[{
                "role": "user",
                "content": f"根据以下数据生成一个 Dashboard 的 ui_schema JSON: {data}"
            }],
        )
        return NodeResult(
            status="success",
            result={"ui_schema": ui_schema},
            ui_schema=ui_schema,  # 直接作为前端渲染配置
        )
```

### 6. 测试

- `tests/unit/test_dashscope_wrapper.py`：Mock DashScope API
- `tests/unit/test_llm_call_node.py`：对话节点
- `tests/unit/test_llm_extract_node.py`：结构化抽取
- `tests/unit/test_embedding_node.py`：向量化

### 7. 验收

- [ ] llm_call 节点可在流程中调用 DashScope 并返回结果
- [ ] llm_extract 节点可从文本抽取结构化 JSON
- [ ] embedding 节点可将文本转为向量
- [ ] ui_schema_gen 节点可生成 Dashboard 配置
- [ ] streaming 对话可通过 `/api/llm/chat?stream=true` SSE 返回
