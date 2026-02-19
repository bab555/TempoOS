# 数字员工平台 — 前端对接文档

> **版本**: v3.0
> **日期**: 2026-02-15
> **核心理念**: 中控模型驱动 + A2UI 可视化 + OSS 文件闭环 + SSE 流式响应

---

## 一、核心交互模式

前端只需关注 **"中控对话"** 和 **"可视化渲染"** 两个核心环节。

### 1.1 产品形态

类似"元宝 / 豆包"的对话式 AI 助手：
- **左侧**：对话气泡（流式文字输出）。
- **右侧**：可视化面板（表格 / 文档预览 / 报表），弱交互（仅下载、重新生成等简单操作）。

### 1.2 交互流程

1.  **用户输入**：用户在聊天框输入文本，或附带文件。
2.  **中控决策**：调用 `POST /api/agent/chat`，后端 LLM 自动判断该直接回复，还是调用工具（搜索/撰写/查数据）。
3.  **SSE 流式响应**：后端通过 Server-Sent Events 实时推送，前端根据 `event` 类型分发到不同 UI 区域。
4.  **文件处理**：所有文件上传走 OSS 直传，后端只接收 OSS URL。

**不需要 WebSocket**。右侧面板是结果展示，用户的后续操作（如"重新生成"）就是一次新的对话请求。

---

## 二、接口

只有一个核心接口。

### POST `/api/agent/chat`

**Headers** (必传):

| Header | 说明 | 示例 |
|--------|------|------|
| `X-Tenant-Id` | 租户隔离 | `default` |
| `X-User-Id` | 用户隔离（前端生成的 UUID，存 localStorage） | `a1b2c3d4-...` |
| `Accept` | 标识 SSE | `text/event-stream` |

**Request Body**:

```json
{
  "session_id": null,
  "messages": [
    {
      "role": "user",
      "content": "帮我搜索3款i7笔记本做个比价表",
      "files": [
        {
          "name": "需求清单.xlsx",
          "url": "https://oss.example.com/bucket/xxx.xlsx",
          "type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        }
      ]
    }
  ],
  "context": {
    "current_page": "procurement"
  }
}
```

**字段说明**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | 否 | 首次传 `null`，后端返回后存下来，后续请求带上（保持上下文） |
| `messages` | array | 是 | 对话消息列表 |
| `messages[].role` | string | 是 | 固定传 `"user"` |
| `messages[].content` | string | 是 | 用户输入的文本 |
| `messages[].files` | array | 否 | 附件列表（OSS URL） |
| `context` | object | 否 | 前端上下文（当前页面等），后端可用于辅助决策 |

---

## 三、SSE 响应事件

后端通过 SSE 流式返回，每个事件由 `event` 和 `data` 两行组成。

### 3.1 事件类型总览

| event | 含义 | 前端处理 |
|-------|------|----------|
| `session_init` | 会话初始化 | 存储 `session_id` |
| `thinking` | 后端正在执行某个任务 | 展示 Loading 状态文字 |
| `message` | 对话文本（逐段流式） | **左侧气泡** 追加文字 |
| `tool_start` | 开始执行某个工具 | 可选展示"正在搜索…" |
| `tool_done` | 工具执行完毕 | 可选隐藏 Loading |
| `ui_render` | A2UI 组件数据 | **右侧面板** 渲染组件 |
| `ping` | 心跳（可选） | 保持连接/代理不超时 |
| `error` | 出错 | 展示错误提示 |
| `done` | 本轮对话结束 | 解锁输入框 |

### 3.2 为动画/状态机设计的关键字段（强烈建议按此实现）

为了实现类似“元宝/豆包”的**顺滑动画**（打字机、步骤条、进度条、右侧渐显/替换、失败重试提示），`data` 建议使用结构化字段，而不是仅靠 `content` 文本。

#### 3.2.1 `thinking`（阶段/进度驱动）

```json
{
  "content": "正在执行：联网搜索...",
  "phase": "tool",
  "step": "search",
  "status": "running",
  "progress": 30,
  "run_id": "6b5e6d2a-0e8c-4d51-a7b4-1b7b3e5f3d2f"
}
```

- `phase`: `"plan" | "tool" | "summarize" | "finalize"`
- `step`: `"search" | "writer" | "data_query" | "other"`
- `status`: `"running" | "success" | "failed"`
- `progress`: \(0-100\)，用于进度条/环形进度
- `run_id`: 可选；若当前 thinking 对应某次工具调用，建议带上，便于关联步骤条

#### 3.2.2 `tool_start` / `tool_done`（步骤条/并发工具动画）

`tool_start`：

```json
{
  "run_id": "6b5e6d2a-0e8c-4d51-a7b4-1b7b3e5f3d2f",
  "tool": "search",
  "title": "联网搜索",
  "status": "running",
  "progress": 0
}
```

`tool_done`：

```json
{
  "run_id": "6b5e6d2a-0e8c-4d51-a7b4-1b7b3e5f3d2f",
  "tool": "search",
  "title": "联网搜索",
  "status": "success",
  "progress": 100
}
```

#### 3.2.3 `message`（更稳的打字机/增量合并）

```json
{
  "message_id": "d04b1f2a-4b3a-4f6b-a91a-2fbdbf3f08e8",
  "seq": 12,
  "mode": "delta",
  "role": "assistant",
  "content": "详见右侧比价表。"
}
```

- `mode`: `"delta"` 表示增量分片（打字机）；`"full"` 表示每次都是全文覆盖
- `seq`: 同一 `message_id` 下严格递增，用于防乱序/断线重放时去重

#### 3.2.4 `ui_render`（右侧动画：replace/append/patch）

```json
{
  "schema_version": 1,
  "ui_id": "panel_main",
  "render_mode": "replace",
  "component": "smart_table",
  "title": "笔记本比价表",
  "data": { "columns": [], "rows": [] },
  "actions": []
}
```

- `schema_version`: UI schema 版本号（用于前端兼容）
- `ui_id`: 指定更新的 UI 容器/卡片（便于做动画过渡）
- `render_mode`:
  - `"replace"`：整体替换（最常用，适合渐隐渐现）
  - `"append"`：追加一张新卡片（时间线式结果）
  - `"patch"`：局部更新（如进度/行追加；前端自行定义 patch 语义）

#### 3.2.5 `error`（错误码 + 是否可重试）

```json
{
  "code": "RATE_LIMITED",
  "message": "请求过于频繁，请稍后重试",
  "retryable": true
}
```

建议 `code` 枚举：`BAD_REQUEST` / `UNAUTHORIZED` / `FORBIDDEN` / `RATE_LIMITED` / `UPSTREAM_ERROR` / `INTERNAL_ERROR`

#### 3.2.6 `ping`（可选心跳）

```json
{ "ts": 1739580000000 }
```

前端可忽略，只用于保持连接活性（避免代理超时）。

### 3.3 完整示例（包含 run_id / message_id / render_mode）

```text
event: session_init
data: {"session_id": "550e8400-e29b-41d4-a716-446655440000"}

event: thinking
data: {"content": "正在思考...", "phase": "plan", "status": "running", "progress": 5}

event: tool_start
data: {"run_id": "6b5e6d2a-0e8c-4d51-a7b4-1b7b3e5f3d2f", "tool": "search", "title": "联网搜索", "status": "running", "progress": 0}

event: thinking
data: {"content": "正在执行：联网搜索...", "phase": "tool", "step": "search", "status": "running", "progress": 30, "run_id": "6b5e6d2a-0e8c-4d51-a7b4-1b7b3e5f3d2f"}

event: tool_done
data: {"run_id": "6b5e6d2a-0e8c-4d51-a7b4-1b7b3e5f3d2f", "tool": "search", "title": "联网搜索", "status": "success", "progress": 100}

event: ui_render
data: {"schema_version": 1, "ui_id": "panel_main", "render_mode": "replace", "component": "smart_table", "title": "笔记本比价表", "data": {"columns": [{"key": "supplier", "label": "供应商"}, {"key": "price", "label": "价格"}], "rows": [{"supplier": "京东自营", "price": 9999}]}, "actions": [{"label": "导出 Excel", "action_type": "download_json_as_xlsx"}]}

event: thinking
data: {"content": "正在整理结果...", "phase": "summarize", "status": "running", "progress": 85}

event: message
data: {"message_id": "d04b1f2a-4b3a-4f6b-a91a-2fbdbf3f08e8", "seq": 1, "mode": "delta", "role": "assistant", "content": "已为您找到"}

event: message
data: {"message_id": "d04b1f2a-4b3a-4f6b-a91a-2fbdbf3f08e8", "seq": 2, "mode": "delta", "role": "assistant", "content": "3家供应商的报价，"}

event: message
data: {"message_id": "d04b1f2a-4b3a-4f6b-a91a-2fbdbf3f08e8", "seq": 3, "mode": "delta", "role": "assistant", "content": "详见右侧比价表。"}

event: done
data: {"session_id": "550e8400-e29b-41d4-a716-446655440000"}
```

### 3.4 前端监听代码参考（支持 message_id/seq + 状态动画）

```javascript
const response = await fetch('/api/agent/chat', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Accept': 'text/event-stream',
    'X-Tenant-Id': 'default',
    'X-User-Id': getUserId(),  // localStorage 中的 UUID
  },
  body: JSON.stringify(requestBody),
});

const reader = response.body.getReader();
const decoder = new TextDecoder();
let buffer = '';

const messageBuffers = new Map(); // message_id -> {seq, text}

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  
  buffer += decoder.decode(value, { stream: true });
  const lines = buffer.split('\n');
  buffer = lines.pop();  // 保留未完成的行
  
  let currentEvent = '';
  for (const line of lines) {
    if (line.startsWith('event: ')) {
      currentEvent = line.slice(7);
    } else if (line.startsWith('data: ') && currentEvent) {
      const data = JSON.parse(line.slice(6));
      
      switch (currentEvent) {
        case 'message':
          // 更稳的打字机：按 message_id + seq 合并
          if (data.mode === 'delta' && data.message_id) {
            const prev = messageBuffers.get(data.message_id) || { seq: 0, text: '' };
            if (data.seq > prev.seq) {
              prev.seq = data.seq;
              prev.text += data.content || '';
              messageBuffers.set(data.message_id, prev);
              upsertAssistantBubble(data.message_id, prev.text); // → 左侧气泡（同一条消息持续更新）
            }
          } else {
            appendToChatBubble(data.content);   // 兼容旧格式
          }
          break;
        case 'ui_render':
          renderRightPanel(data);              // → 右侧面板（可用 ui_id + render_mode 做动画）
          break;
        case 'thinking':
          // 可用 phase/progress 驱动步骤条/进度条动画
          showLoadingStatus(data.content, data);
          break;
        case 'tool_start':
        case 'tool_done':
          updateToolTimeline(data);            // 可选：步骤条/并发工具动画（run_id 关联）
          break;
        case 'ping':
          // ignore
          break;
        case 'session_init':
          saveSessionId(data.session_id);
          break;
        case 'done':
          enableInput();
          break;
        case 'error':
          showError(data.message, data.code);
          break;
      }
      currentEvent = '';
    }
  }
}
```

### 3.5 断线重连与幂等（建议实现）

- **断线重连**：前端可以重新发起一次 `/api/agent/chat` 请求（带上同一个 `session_id`），用于继续同一会话上下文。
- **幂等/去重**：前端应使用 `message_id + seq` 做去重合并；`tool_*` 使用 `run_id` 关联步骤条状态；`ui_render` 使用 `ui_id` 决定替换哪块区域。
- **注意**：本阶段 SSE 不保证“断线后从中断位置继续推流”，因此重连更像“继续对话”，不是“恢复同一条流”。

---

## 四、右侧可视化面板 (A2UI)

当 `event: ui_render` 时，前端根据 `component` 字段渲染对应组件。
**设计原则**：右侧是结果展示板，不做复杂编辑。

### 4.1 表格 (`component: "smart_table"`)

用于：比价表、报价单、数据清单。

```json
{
  "component": "smart_table",
  "title": "笔记本比价表",
  "data": {
    "columns": [
      {"key": "supplier", "label": "供应商", "width": 150},
      {"key": "price", "label": "价格", "type": "currency"},
      {"key": "rating", "label": "好评率", "type": "progress"}
    ],
    "rows": [
      {"supplier": "京东自营", "price": 9999, "rating": 98}
    ]
  },
  "actions": [
    {"label": "导出 Excel", "action_type": "download_json_as_xlsx"},
    {"label": "重新搜索", "action_type": "post_back", "payload": "换一批"}
  ]
}
```

### 4.2 文档预览 (`component: "document_preview"`)

用于：合同、送货单。

```json
{
  "component": "document_preview",
  "title": "采购合同_20260215",
  "data": {
    "fields": {"party_a": "中建四局", "amount": "500,000"},
    "sections": [{"title": "第一条", "content": "本合同..."}]
  },
  "actions": [
    {"label": "下载 Word", "action_type": "download_generated_file"},
    {"label": "确认归档", "action_type": "post_back", "payload": "确认无误，归档"}
  ]
}
```

### 4.3 报表 (`component: "chart_report"`)

用于：财务报表、数据分析。

```json
{
  "component": "chart_report",
  "title": "1月财务对账",
  "data": {
    "metrics": [
      {"label": "总金额", "value": "100万"},
      {"label": "差额", "value": "2万", "status": "warning"}
    ],
    "charts": [{"type": "bar", "title": "采购额", "x": ["A", "B"], "y": [50, 30]}]
  }
}
```

### 4.4 图片预览 (`component: "image_preview"`)

用于：CAD 图纸转换结果、扫描件等。

```json
{
  "component": "image_preview",
  "title": "建筑平面图",
  "data": {
    "url": "https://oss.../preview.png",
    "download_url": "https://oss.../converted.dxf",
    "metadata": [{"label": "图层数", "value": "15"}]
  },
  "actions": [{"label": "下载源文件", "action_type": "download"}]
}
```

### 4.5 actions 中的 `action_type`

| action_type | 含义 | 前端处理 |
|-------------|------|----------|
| `download_json_as_xlsx` | 将表格数据导出为 Excel | 前端用 `xlsx` 库生成 |
| `download_generated_file` | 将文档数据导出为 Word | 前端用 `docx-js` 库生成 |
| `download` | 直接下载 URL 指向的文件 | `window.open(url)` |
| `post_back` | 将 `payload` 作为新消息发回后端 | 发起新的 `/api/agent/chat` 请求 |

---

## 五、文件上传

前端直传 OSS，**不经过后端服务器**。

### 5.1 获取直传签名（后端提供）

> 说明：浏览器直传 OSS 需要短期有效的签名（policy/signature）。该签名由后端生成并返回，前端**只拿到可用字段**，不会接触 AccessKeySecret。

**POST** `/api/oss/post-signature`

**Headers**:
- `X-Tenant-Id: default`
- `X-User-Id: <uuid>`（可选）

**Request Body**:

```json
{
  "filename": "模板.docx",
  "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "dir": "templates/",
  "expire_seconds": 600
}
```

**Response**:

```json
{
  "upload": {
    "method": "POST",
    "url": "https://<bucket>.<endpoint>",
    "fields": {
      "key": "tempoos/tenant/default/user/<user>/2026/02/17/templates/<uuid>_模板.docx",
      "policy": "<base64>",
      "OSSAccessKeyId": "<id>",
      "success_action_status": "200",
      "signature": "<sig>"
    },
    "expire_at": 1739580600
  },
  "object": {
    "bucket": "<bucket>",
    "endpoint": "<endpoint>",
    "key": "....docx",
    "url": "https://<bucket>.<endpoint>/....docx"
  }
}
```

### 5.2 使用签名直传 OSS（前端执行）

```javascript
async function uploadToOss(file) {
  const signResp = await fetch('/api/oss/post-signature', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Tenant-Id': 'default',
      'X-User-Id': getUserId(),
    },
    body: JSON.stringify({
      filename: file.name,
      content_type: file.type,
      dir: 'uploads/',
      expire_seconds: 600,
    }),
  }).then(r => r.json());

  const form = new FormData();
  Object.entries(signResp.upload.fields).forEach(([k, v]) => form.append(k, v));
  form.append('file', file);

  const uploadRes = await fetch(signResp.upload.url, {
    method: 'POST',
    body: form,
  });
  if (!uploadRes.ok) throw new Error('OSS upload failed');

  return signResp.object.url; // 这个就是后续传给 /api/agent/chat 的 files[].url
}
```

### 5.3 把 OSS URL 传给 Agent

上传成功拿到 `oss_url` 后，在 `/api/agent/chat` 的 `messages[].files[]` 里带上：
- `name`
- `url`（OSS URL）
- `type`

### 5.4 文件处理链路（前端无需关心，仅供了解）

前端只需要做"上传 → 传 URL"，后端自动完成文件解析。内部链路如下：

```
前端上传到OSS
    ├── 快速路径: OSS 回调 → Tonglu 解析 → 发 FILE_READY 事件
    └── 兜底路径: /api/agent/chat 带 files → Agent Controller 发 FILE_UPLOADED 事件 → Tonglu 监听并解析 → 发 FILE_READY 事件
                                                                                                          ↓
                                                                                          Agent Controller 收到 FILE_READY
                                                                                                          ↓
                                                                                          文件文本内容注入 LLM 上下文
                                                                                                          ↓
                                                                                          LLM 正常决策（文件内容已变成文字）
```

**对前端的影响**：
- 如果附带了文件，SSE 流开头会多一个 `thinking` 事件：`"正在处理上传文件..."`
- 处理完成后正常进入 LLM 对话流程，前端无需额外操作
- 超时（默认 60 秒）后会降级为提示文本，不会卡死

---

## 六、Q&A

**Q: session_id 怎么管理？**
A: 首次请求传 `null`，后端在 `session_init` 事件中返回。前端存下来，后续对话带上。新开话题就传 `null` 重新获取。

**Q: X-User-Id 怎么生成？**
A: 前端首次访问时生成一个 UUID 存到 localStorage，后续每次请求都带上。不需要登录。

**Q: 后端怎么决定调哪个工具？**
A: 前端不需要关心。后端 LLM 自动根据用户输入决定调用搜索/撰写/查数据。前端只需要处理返回的 `message` 和 `ui_render` 事件。

**Q: 右侧面板遇到未知的 component 类型怎么办？**
A: 降级为通用卡片（显示标题 + JSON 数据 + 下载按钮）。后端可能会随版本新增组件类型，前端按需跟进实现。
