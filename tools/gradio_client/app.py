# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
TempoOS Gradio Client — A lightweight frontend for testing the Agent API.

Features:
- SSE Streaming support (real-time thinking & response)
- A2UI Visualization (Table, Document, JSON)
- File Upload (Direct OSS upload simulation)
- Debug Panel (Raw event log)

Usage:
    pip install -r requirements.txt
    python app.py
"""

import json
import uuid
import os
import time
import mimetypes
from typing import List, Dict, Any, Generator, Tuple

import gradio as gr
import httpx

# ── Configuration ─────────────────────────────────────────────────────────────

DEFAULT_SERVER_URL = os.getenv("TEMPO_SERVER_URL", "http://42.121.216.117:8200")
DEFAULT_TENANT_ID = os.getenv("TEMPO_TENANT_ID", "default")
DEFAULT_USER_ID = os.getenv("TEMPO_USER_ID", "gradio_tester_001")

# ── Helpers ───────────────────────────────────────────────────────────────────

def generate_session_id():
    return str(uuid.uuid4())

def parse_sse_line(line: str) -> Tuple[str, str]:
    """Parse 'event: ...' or 'data: ...' lines."""
    if line.startswith("event:"):
        return "event", line[6:].strip()
    if line.startswith("data:"):
        return "data", line[5:].strip()
    return None, None

async def upload_file_to_oss(file_obj, server_url: str, tenant_id: str, user_id: str) -> Dict[str, Any]:
    """
    1. Get signature from backend
    2. Upload to OSS directly
    3. Return file info for Agent
    """
    if not file_obj:
        return None

    filename = os.path.basename(file_obj.name)
    mime_type, _ = mimetypes.guess_type(filename)
    mime_type = mime_type or "application/octet-stream"

    # 1. Get Signature
    async with httpx.AsyncClient() as client:
        sig_resp = await client.post(
            f"{server_url}/api/oss/post-signature",
            json={
                "filename": filename,
                "content_type": mime_type,
                "dir": "gradio_uploads/",
            },
            headers={"X-Tenant-Id": tenant_id, "X-User-Id": user_id},
        )
        if sig_resp.status_code != 200:
            raise Exception(f"Get signature failed: {sig_resp.text}")
        
        sig_data = sig_resp.json()
        upload_info = sig_data["upload"]
        object_info = sig_data["object"]

    # 2. Upload to OSS
    with open(file_obj.name, "rb") as f:
        file_content = f.read()
    
    files = {"file": (filename, file_content, mime_type)}
    data = upload_info["fields"]

    async with httpx.AsyncClient() as client:
        oss_resp = await client.post(upload_info["url"], data=data, files=files)
        if oss_resp.status_code not in (200, 204):
             raise Exception(f"OSS upload failed: {oss_resp.text}")

    # 3. Return FileRef
    return {
        "name": filename,
        "url": object_info["url"],
        "type": mime_type
    }

# ── Core Logic ────────────────────────────────────────────────────────────────

async def chat_stream(
    message: str,
    history: List[Dict[str, str]],
    files: List[Any],
    session_state: Dict[str, Any],
    server_url: str,
    tenant_id: str,
    user_id: str
) -> Generator:
    """
    Main chat handler.
    Yields updates to: Chatbot, History, UI Panels, Debug Log.
    """
    if not message and not files:
        yield history, session_state, gr.update(), gr.update(), gr.update(), "No input"
        return

    # Prepare session
    session_id = session_state.get("session_id")
    
    # Upload files first
    uploaded_files = []
    debug_log = "--- Request ---\n"
    
    if files:
        debug_log += f"Uploading {len(files)} files...\n"
        # Add user message with file placeholder
        history.append({"role": "user", "content": message + "\n[⏳ 正在上传文件...]"})
        yield history, session_state, gr.update(), gr.update(), gr.update(), debug_log
        
        try:
            for f in files:
                f_ref = await upload_file_to_oss(f, server_url, tenant_id, user_id)
                uploaded_files.append(f_ref)
                debug_log += f"Uploaded: {f_ref['name']} -> {f_ref['url']}\n"
            # Update user message to remove placeholder
            history[-1]["content"] = message
        except Exception as e:
            err_msg = f"❌ 文件上传失败: {str(e)}"
            history[-1]["content"] += f"\n{err_msg}"
            yield history, session_state, gr.update(), gr.update(), gr.update(), debug_log
            return
    else:
        history.append({"role": "user", "content": message})

    # Prepare Request
    payload = {
        "session_id": session_id,
        "messages": [
            {
                "role": "user",
                "content": message,
                "files": uploaded_files
            }
        ],
        "context": {"source": "gradio_client"}
    }
    
    debug_log += f"POST {server_url}/api/agent/chat\nPayload: {json.dumps(payload, ensure_ascii=False)}\n\n--- Response Stream ---\n"
    
    # Initial UI state - add empty assistant message
    history.append({"role": "assistant", "content": ""})
    yield history, session_state, gr.update(), gr.update(), gr.update(), debug_log

    # SSE Request
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            async with client.stream(
                "POST",
                f"{server_url}/api/agent/chat",
                json=payload,
                headers={
                    "X-Tenant-Id": tenant_id,
                    "X-User-Id": user_id,
                    "Accept": "text/event-stream"
                }
            ) as response:
                
                if response.status_code != 200:
                    err = f"Server Error: {response.status_code}"
                    history[-1]["content"] = f"❌ {err}"
                    yield history, session_state, gr.update(), gr.update(), gr.update(), debug_log + f"\n{err}"
                    return

                buffer = ""
                current_event_type = None
                
                # UI State holders
                table_data = None
                doc_content = None
                raw_json = None
                
                assistant_text = ""
                thinking_text = ""

                async for chunk in response.aiter_lines():
                    chunk = chunk.strip()
                    if not chunk:
                        continue
                    
                    key, value = parse_sse_line(chunk)
                    
                    if key == "event":
                        current_event_type = value
                    elif key == "data":
                        try:
                            data = json.loads(value)
                            debug_log += f"[{current_event_type}] {json.dumps(data, ensure_ascii=False)}\n"
                            
                            # ── Event Handling ──

                            if current_event_type == "session_init":
                                session_state["session_id"] = data.get("session_id")
                            
                            elif current_event_type == "thinking":
                                # Show thinking process in chat
                                content = data.get("content", "")
                                phase = data.get("phase", "")
                                if content and content != thinking_text:
                                    thinking_text = content
                                    display_text = assistant_text + f"\n\n> 🧠 **思考中**: {content}"
                                    history[-1]["content"] = display_text
                                    yield history, session_state, gr.update(), gr.update(), gr.update(), debug_log

                            elif current_event_type == "tool_start":
                                tool = data.get("title", data.get("tool", "Unknown"))
                                display_text = assistant_text + f"\n\n> 🛠️ **调用工具**: {tool}..."
                                history[-1]["content"] = display_text
                                yield history, session_state, gr.update(), gr.update(), gr.update(), debug_log

                            elif current_event_type == "message":
                                # Append text
                                content = data.get("content", "")
                                assistant_text += content
                                history[-1]["content"] = assistant_text
                                yield history, session_state, gr.update(), gr.update(), gr.update(), debug_log

                            elif current_event_type == "ui_render":
                                # Update Right Panel
                                component = data.get("component")
                                raw_json = data
                                
                                if component == "smart_table":
                                    # Convert to Dataframe-friendly format
                                    cols = [c["label"] for c in data["data"]["columns"]]
                                    col_keys = [c["key"] for c in data["data"]["columns"]]
                                    rows = []
                                    for r in data["data"]["rows"]:
                                        rows.append([r.get(k, "") for k in col_keys])
                                    table_data = {"headers": cols, "data": rows}
                                    
                                    yield history, session_state, \
                                          gr.update(value=table_data, visible=True), \
                                          gr.update(visible=False), \
                                          gr.update(value=raw_json, visible=True), \
                                          debug_log

                                elif component == "document_preview":
                                    # Convert to Markdown
                                    md = f"# {data.get('title', '文档')}\n\n"
                                    for sec in data["data"].get("sections", []):
                                        md += f"## {sec.get('title', '')}\n\n{sec.get('content', '')}\n\n"
                                    doc_content = md
                                    
                                    yield history, session_state, \
                                          gr.update(visible=False), \
                                          gr.update(value=doc_content, visible=True), \
                                          gr.update(value=raw_json, visible=True), \
                                          debug_log
                                
                                else:
                                    # Fallback: just show JSON
                                    yield history, session_state, \
                                          gr.update(visible=False), \
                                          gr.update(visible=False), \
                                          gr.update(value=raw_json, visible=True), \
                                          debug_log

                            elif current_event_type == "error":
                                err_msg = f"\n\n❌ **Error**: {data.get('message')}"
                                assistant_text += err_msg
                                history[-1]["content"] = assistant_text
                                yield history, session_state, gr.update(), gr.update(), gr.update(), debug_log

                        except json.JSONDecodeError:
                            pass

        except Exception as e:
            err = f"Connection Error: {str(e)}"
            history[-1]["content"] += f"\n\n❌ {err}"
            yield history, session_state, gr.update(), gr.update(), gr.update(), debug_log + f"\n{err}"


# ── UI Layout ─────────────────────────────────────────────────────────────────

with gr.Blocks(title="TempoOS 测试台", theme=gr.themes.Soft()) as demo:
    state = gr.State({}) # Store session_id

    with gr.Row():
        gr.Markdown("## 🤖 TempoOS 数字员工 - 测试台")
    
    with gr.Row():
        with gr.Column(scale=1):
            # Settings
            with gr.Accordion("⚙️ 连接设置", open=False):
                server_url = gr.Textbox(label="Server URL", value=DEFAULT_SERVER_URL)
                tenant_id = gr.Textbox(label="Tenant ID", value=DEFAULT_TENANT_ID)
                user_id = gr.Textbox(label="User ID", value=DEFAULT_USER_ID)
            
            # Chatbot
            chatbot = gr.Chatbot(label="对话", height=600, type="messages")
            
            # Input
            with gr.Row():
                msg_input = gr.Textbox(
                    show_label=False, 
                    placeholder="输入指令，例如：'帮我搜索一下 ThinkPad 价格'...",
                    scale=4,
                    container=False
                )
                file_btn = gr.UploadButton("📁", file_types=["file"], scale=1, size="sm")
                send_btn = gr.Button("发送", variant="primary", scale=1)
            
            # File list display
            file_display = gr.File(label="已选文件", file_count="multiple", height=100, interactive=False, visible=False)

        with gr.Column(scale=1):
            # Right Panel (A2UI)
            gr.Markdown("### 📊 可视化面板 (A2UI)")
            
            # Dynamic Components
            out_table = gr.Dataframe(label="表格视图", visible=False, wrap=True)
            out_doc = gr.Markdown(label="文档视图", visible=False)
            out_json = gr.JSON(label="原始数据 (Debug)", visible=True)
            
            # Debug Log
            with gr.Accordion("📝 调试日志 (SSE Stream)", open=True):
                debug_output = gr.Code(language="json", label="Log", interactive=False, lines=20)

    # ── Event Bindings ──

    # Handle file selection
    def on_file_upload(files):
        return gr.update(value=files, visible=True)

    file_btn.upload(on_file_upload, file_btn, file_display)

    # Handle send
    send_btn.click(
        chat_stream,
        inputs=[msg_input, chatbot, file_display, state, server_url, tenant_id, user_id],
        outputs=[chatbot, state, out_table, out_doc, out_json, debug_output]
    ).then(
        lambda: (None, None), # Clear input and files after send
        None,
        [msg_input, file_display]
    )

    msg_input.submit(
        chat_stream,
        inputs=[msg_input, chatbot, file_display, state, server_url, tenant_id, user_id],
        outputs=[chatbot, state, out_table, out_doc, out_json, debug_output]
    ).then(
        lambda: (None, None),
        None,
        [msg_input, file_display]
    )

if __name__ == "__main__":
    demo.queue().launch(server_name="0.0.0.0", server_port=7860)
