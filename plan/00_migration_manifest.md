# 代码迁移清单（ctrlc → tempo_os）

> **前置条件**：已将 tempo-core、nexus-supply、数字员工2、铜炉poc 的代码放入 `ctrlc/` 目录
> **执行方式**：运行 `scripts/migrate_ctrlc_to_tempo_os.ps1` 或按本文档手动操作

---

## 一、tempo-core → tempo_os（内核，必须迁入）

| 源路径 (ctrlc) | 目标路径 (tempo_os) |
|----------------|---------------------|
| `tempo-core/tempo/kernel/*.py` | `tempo_os/kernel/` |
| `tempo-core/tempo/kernel/__init__.py` | `tempo_os/kernel/` |
| `tempo-core/tempo/memory/*.py` | `tempo_os/memory/` |
| `tempo-core/tempo/protocols/*.py` | `tempo_os/protocols/` |
| `tempo-core/tempo/core/*.py` | `tempo_os/core/` |
| `tempo-core/tempo/runtime/*.py` | `tempo_os/runtime/` |
| `tempo-core/tempo/workers/base.py` | `tempo_os/workers/` |
| `tempo-core/tempo/workers/__init__.py` | `tempo_os/workers/` |
| `tempo-core/tempo/workers/std/echo.py` | `tempo_os/workers/std/` |
| `tempo-core/tempo/workers/std/__init__.py` | `tempo_os/workers/std/` |
| `tempo-core/tempo/__init__.py` | `tempo_os/` |

**不迁入**：`api/server.py`、`demo/`、`prompts/`、`main.py`、`check_env.py`

---

## 二、tempo-core 测试 → tests/

| 源路径 | 目标路径 |
|--------|----------|
| `tempo-core/tests/conftest.py` | 合并到 `tests/conftest.py` |
| `tempo-core/tests/unit/*.py` | `tests/unit/` |
| `tempo-core/tests/scenarios/*.py` | `tests/scenarios/` |
| `tempo-core/tests/e2e/*.py` | `tests/e2e/` |
| `tempo-core/tests/utils/*.py` | `tests/utils/` |

---

## 三、nexus-supply → tempo_os/nodes/biz/supply/

| 源路径 | 目标路径 |
|--------|----------|
| `nexus-supply/app/agents/subgraphs/sourcing_agent.py` | `tempo_os/nodes/biz/supply/sourcing_agent.py` |
| `nexus-supply/app/agents/subgraphs/quoting_agent.py` | `tempo_os/nodes/biz/supply/quoting_agent.py` |
| `nexus-supply/app/agents/subgraphs/writer_agent.py` | `tempo_os/nodes/biz/supply/writer_agent.py` |
| `nexus-supply/app/agents/subgraphs/finance_agent.py` | `tempo_os/nodes/biz/supply/finance_agent.py` |
| `nexus-supply/app/models/db_models.py` | `tempo_os/nodes/biz/supply/supply_models.py` |
| `nexus-supply/app/services/document_generator.py` | `tempo_os/nodes/biz/supply/document_generator.py` |

**不迁入**：master_agent、graph、redis_context、aliyun_llm、api/、core/、agents/prompts、agents/skills、agents/chat_handlers、frontend

---

## 四、数字员工2 → tempo_os/nodes/biz/cad/

| 源路径 | 目标路径 |
|--------|----------|
| `数字员工2/src/worker/inspector.py` | `tempo_os/nodes/biz/cad/inspector.py` |
| `数字员工2/src/worker/compiler.py` | `tempo_os/nodes/biz/cad/compiler.py` |
| `数字员工2/src/worker/sandbox.py` | `tempo_os/nodes/biz/cad/sandbox.py` |
| `数字员工2/src/worker/dxf_parser.py` | `tempo_os/nodes/biz/cad/dxf_parser.py` |
| `数字员工2/src/worker/converter.py` | `tempo_os/nodes/biz/cad/converter.py` |

**不迁入**：llm.py、main.py、server.py、agent/、web_ui、features、web/

---

## 五、铜炉 PoC → tempo_os/services/tonglu/（可复用算法）

| 源路径 | 目标路径 |
|--------|----------|
| `铜炉poc/apps/tonglu_poc/storage/schema_registry.py` | `tempo_os/services/tonglu/schema_registry.py` |
| `铜炉poc/apps/tonglu_poc/storage/anchor_registry.py` | `tempo_os/services/tonglu/anchor_registry.py` |
| `铜炉poc/apps/tonglu_poc/storage/embedding.py` | `tempo_os/services/tonglu/embedding.py` |
| `铜炉poc/apps/tonglu_poc/storage/schema.sql` | `tempo_os/services/tonglu/schema.sql` |
| `铜炉poc/apps/tonglu_poc/storage/models.py` | `tempo_os/services/tonglu/models.py` |
| `铜炉poc/apps/tonglu_poc/services/ingest_service.py` | `tempo_os/services/tonglu/ingest_service.py` |
| `铜炉poc/apps/tonglu_poc/parsers/base.py` | `tempo_os/services/tonglu/parsers/base.py` |
| `铜炉poc/apps/tonglu_poc/parsers/csv_parser.py` | `tempo_os/services/tonglu/parsers/csv_parser.py` |
| `铜炉poc/apps/tonglu_poc/parsers/json_parser.py` | `tempo_os/services/tonglu/parsers/json_parser.py` |
| `铜炉poc/apps/tonglu_poc/models.py` | `tempo_os/services/tonglu/models_pydantic.py` |

**不迁入**：server.py、config.py、mock_engine.py、db_manager.py、vector_store.py、langgraph_adapter、routers/*、run_manager、static/

---

## 六、迁移后必须执行

1. **全局替换 import**：`from tempo.` → `from tempo_os.`，`import tempo.` → `import tempo_os.`
2. **创建 __init__.py**：确保每个目录有 `__init__.py`
3. **删除 ctrlc 中多余文件**：见 `scripts/cleanup_ctrlc.ps1`
