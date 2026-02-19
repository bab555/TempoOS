# Plan 18：端到端交互验收 — CAD 编辑流程（不做前端渲染）
> **Task ID**: `18_e2e_cad`
> **目标**: 完整跑通"上传 DWG→解析→AI 编辑→确认/回滚→导出"，验证 CAD 节点全链路的**交互能力**：HTTP API 可推进、WS 可订阅、事件中包含可校验的 `ui_schema`
> **依赖**: `14_cad_nodes`, `16_a2ui_protocol`
> **预估**: 2 天

---

## 验证场景

### 场景：编辑配电箱图纸

```
用户操作                          平台行为                               交互验收（不渲染UI）
─────────────────────────────────────────────────────────────────────────────────
1. 上传 DWG 文件                 → file_parser 节点：DWG→DXF 转换      → WS 收到上传/解析进度事件
                                 → 启动 cad_edit_flow

2. cad_inspect 执行              → 提取 Manifest (layers/blocks/bbox)  → WS 收到 ui_schema（markdown/table/selection_list 任意组合）
                                 → STEP_DONE → waiting_user             → ui_schema 合约校验通过

3. 用户输入编辑指令              → USER_CONFIRM + payload:{instruction} → HTTP 推进成功 + WS 收到执行进度
   "在空白区域添加一个新的        → cad_modify 节点执行
    配电箱回路"                   → LLM 编译为 ezdxf 脚本 → 沙箱执行

4. cad_modify 完成               → 返回 diff (added/removed/modified)  → WS 收到 ui_schema（diff 摘要 + action_buttons）
                                 → STEP_DONE → waiting_user             → ui_schema 合约校验通过

5a. 用户确认                     → USER_CONFIRM → end                  → WS 收到 file_preview/download 的 ui_schema
    → 保存修改后 DXF/DWG         → 铜炉持久化设备清单

5b. 用户回滚                     → USER_ROLLBACK → modify              → HTTP 推进成功，WS 收到回退状态事件
    → 重新输入指令
```

### 验证检查清单

**工作流引擎**:
- [ ] cad_edit_flow 正确推进
- [ ] 回滚操作（USER_ROLLBACK）可回到 modify 状态重新执行

**节点执行**:
- [ ] cad_inspect 解析 DXF 返回 Manifest
- [ ] cad_modify 编译 Python 脚本并在沙箱中执行
- [ ] 修改后 DXF 文件可正确保存
- [ ] diff 计算准确

**数据流转**:
- [ ] cad_manifest 通过 Blackboard 从 inspect → modify 传递
- [ ] cad_output / cad_diff 写入 Blackboard
- [ ] 铜炉可持久化设备清单数据

**前端**:
- [ ] WS 收到每步的 ui_schema（不要求渲染）
- [ ] 用户事件（USER_CONFIRM/USER_ROLLBACK）可通过 HTTP 推进流程
- [ ] 每步收到的 `ui_schema` 均通过合约校验（Plan 16）
