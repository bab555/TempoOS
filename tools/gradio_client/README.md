# TempoOS Gradio 测试台

这是一个轻量级的 Gradio 客户端，用于测试 TempoOS 的 Agent 接口和 A2UI 可视化效果。

## 功能

- **左侧对话**：支持流式输出、思考过程展示 (`thinking`)、工具调用状态 (`tool_start`)。
- **右侧可视化**：根据后端返回的 `ui_render` 事件，动态展示：
  - 表格 (`smart_table`) -> 渲染为可交互 Dataframe
  - 文档 (`document_preview`) -> 渲染为 Markdown
  - 原始 JSON -> 方便调试字段
- **文件上传**：模拟前端直传 OSS 流程（获取签名 -> 直传 -> 发送 URL）。
- **调试日志**：实时显示 SSE 原始事件流。

## 快速开始

### 1. 安装依赖

建议在独立的虚拟环境中运行，或者直接安装：

```bash
cd tools/gradio_client
pip install -r requirements.txt
```

### 2. 运行

```bash
python app.py
```

启动后访问 `http://localhost:7860`。

### 3. 配置

在界面左侧 "⚙️ 连接设置" 中，可以修改：
- **Server URL**: 默认为 `http://42.121.216.117:8200`
- **Tenant ID**: 默认为 `default`
- **User ID**: 默认为 `gradio_tester_001`

## 测试用例

1. **联网搜索**
   - 输入："帮我搜索一下 ThinkPad X1 Carbon 的价格和配置"
   - 预期：左侧显示搜索思考过程，右侧显示比价表格。

2. **文档生成**
   - 输入："生成一份采购合同，甲方是中建四局，金额 50 万"
   - 预期：左侧显示撰写过程，右侧显示合同预览（Markdown）。

3. **文件分析**
   - 点击上传按钮，选择一个 Excel 或 PDF 文件。
   - 输入："分析这个文件"
   - 预期：文件上传成功，Agent 读取文件内容并回答。
