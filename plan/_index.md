# TempoOS 统一平台 — 分步构建计划索引

> **对应文档**: `UNIFIED_PLATFORM_MASTER_PLAN.md` v3.0
> **原则**: 先平台 OS → 再业务节点 → 最后体验层
> **每个 plan 文件 = 一个可独立验收的交付单元**

---

## Phase 1：平台 OS 内核（必须先完成，后续一切依赖此阶段）

| # | Plan | 目标 | 依赖 | 预估 |
|---|------|------|------|------|
| 01 | `01_project_scaffold.md` | 项目骨架、依赖、配置、测试框架 | 无 | 1天 |
| 02 | `02_tempo_core_migration.md` | 从 `数字员工/tempo-core` 迁入内核代码 | 01 | 1天 |
| 03 | `03_pg_storage.md` | PostgreSQL 持久层（sessions/flows/events/幂等日志） | 02 | 2天 |
| 04 | `04_engine_enhance.md` | 工作流引擎增强（双模 Dispatcher + 原子 FSM + 隐式会话） | 02, 03 | 3天 |
| 05 | `05_resilience.md` | 可靠性迁入（幂等/Fan-in/Hard Stop，从 Tempo Kernel 抽取） | 04 | 2天 |
| 06 | `06_node_framework.md` | 内置节点框架（BaseNode + NodeResult + Registry + 基础节点） | 04 | 2天 |
| 07 | `07_api_gateway.md` | FastAPI 网关（workflow/registry/state/llm API + WS + 鉴权） | 04, 06 | 3天 |
| 08 | `08_observability.md` | 最小可观测（trace_id 贯通 + 审计日志 + 关键指标） | 07 | 1天 |
| 08T | `08T_full_unit_test_gate.md` | **全量单元测试闸门**：框架全量 UT/关键集成必须全绿，才允许接入任何业务节点 | 08 | 0.5天 |

**Phase 1 验收标准**: 平台可独立启动，注册一个 echo 节点和一个 YAML 流程，通过 API 启动/推进/完成流程，WebSocket 收到事件推送，PG 有完整审计日志；并且通过 `08T` 的全量测试闸门。

---

## Phase 2：铜炉数据服务 — TempoOS CRM 支撑 (Tonglu v2)

> **策略**: Phase 1 使用 DashScope 商用 API，简单线性 Pipeline + asyncio，20 并发文件处理
> **详细设计**: 参见 `TONGLU_V2_DEV_GUIDE.md`

| # | Plan | 目标 | 依赖 | 预估 |
|---|------|------|------|------|
| 09 | `09_tonglu_scaffold.md` | 铜炉项目骨架 + PG 数据模型 + LLM Service 封装 | 01, 03 | 3天 |
| 10 | `10_tonglu_pipeline.md` | 文件解析器 + 摄入 Pipeline（20 并发）+ 查询引擎 | 09 | 4天 |
| 11 | `11_tonglu_service.md` | HTTP API + Event Sink + TempoOS 节点集成 + 联调 | 08T, 10 | 3天 |

**Phase 2 验收标准**: 上传 PDF/Excel/图片，铜炉自动解析入库；TempoOS 工作流可通过 `data_query` / `data_ingest` / `file_parser` 节点调用铜炉；Blackboard 产物自动沉淀；20 文件并行处理不超时。

---

## Phase 3：业务节点迁入

| # | Plan | 目标 | 依赖 | 预估 |
|---|------|------|------|------|
| 12 | `12_model_gateway.md` | 模型网关节点（统一 DashScope 封装 + streaming + tool calling） | 06 | 2天 |
| 13 | `13_supply_chain_nodes.md` | 供应链节点迁入（sourcing/quoting/doc_writer/finance） | 08T, 12 | 3天 |
| 14 | `14_cad_nodes.md` | CAD 节点迁入（cad_inspect/cad_modify） | 08T | 2天 |
| 15 | `15_doc_nodes.md` | 文档节点迁入（doc_plan/doc_write/doc_export，从玄枢） | 08T, 12 | 2天 |

**Phase 3 验收标准**: 每种业务节点可独立注册、在流程中执行、返回 ui_schema、数据写入 Blackboard。采购全流程端到端可跑。

---

## Phase 4：前端交互能力（不做前端，只做契约与验收）

| # | Plan | 目标 | 依赖 | 预估 |
|---|------|------|------|------|
| 16 | `16_a2ui_protocol.md` | A2UI 协议（后端输出）+ 合约校验 + 示例库（不做前端组件） | 07 | 2天 |
| 17 | `17_e2e_procurement.md` | 端到端交互验收：采购全流程（API+WS+ui_schema 输出） | 13, 16 | 2天 |
| 18 | `18_e2e_cad.md` | 端到端交互验收：CAD 编辑流程（API+WS+ui_schema 输出） | 14, 16 | 2天 |

---

## Phase 5：高级特性（按需，不在本次 plan 范围内）

- 可视化流程编辑器
- 多租户权限与用量计量
- Soft Patch 热更新
- 完整 OpenTelemetry 集成
