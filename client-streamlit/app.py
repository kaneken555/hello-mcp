# client-streamlit/app.py
import json
import os
import requests
import streamlit as st
from dotenv import load_dotenv

# 追加
from mcp_client.transport import MCPTransport

# .env 読み込み
load_dotenv()
MCP_POST_URL = os.getenv("MCP_POST_URL", "http://localhost:3000/messages")
MCP_SSE_URL  = os.getenv("MCP_SSE_URL",  "http://localhost:3000/sse")

st.set_page_config(page_title="Hello MCP Client (SSE)", page_icon="🧩", layout="centered")
st.title("🧩 Hello MCP – Streamlit Client (with SSE)")

# --- セッションスコープでトランスポートを保持 ---
if "transport" not in st.session_state:
    st.session_state.transport = MCPTransport(MCP_SSE_URL, MCP_POST_URL)
if "auto_connected" not in st.session_state:
    st.session_state.auto_connected = False

with st.expander("MCP Settings", expanded=False):
    st.write("POST:", MCP_POST_URL)
    st.write("SSE :", MCP_SSE_URL)

col1, col2, col3 = st.columns(3)
with col1:
    if st.button("🔌 Connect SSE"):
        ok = st.session_state.transport.connect_sse()
        st.success("SSE connected." if ok else "SSE connect failed.")
with col2:
    if st.button("🔌 Auto-connect (once)"):
        st.session_state.auto_connected = True
with col3:
    if st.button("🔴 Disconnect SSE"):
        st.session_state.transport.disconnect_sse()
        st.warning("SSE disconnected.")

# 初回のみ自動接続したいとき
if st.session_state.auto_connected and not st.session_state.transport.is_connected():
    st.session_state.transport.connect_sse()

# 接続状態を表示
status = "✅ Connected" if st.session_state.transport.is_connected() else "⚠️ Not connected"
st.caption(f"SSE Status: {status}")

# デバッグ（最後に受け取ったSSEイベント）
with st.expander("Last SSE Event (debug)", expanded=False):
    st.json(st.session_state.transport.last_event())

# ---- ツール呼び出しUI ----
tool = st.text_input("Tool name", value="say_hello")
params_text = st.text_area("Parameters (JSON)", value='{"name": "Kengo"}', height=120)

if st.button("Call Tool"):
    try:
        # SSEが未接続なら警告
        if not st.session_state.transport.is_connected():
            st.warning("SSEが未接続です。先に『Connect SSE』を押してください。")
            st.stop()
        # endpoint を受け取るまで待機
        if not st.session_state.transport.wait_until_ready():
            st.warning("SSEのendpointが未確立です。もう一度お試しください。")
            st.stop()
        args = json.loads(params_text or "{}")
        payload = {
            "jsonrpc": "2.0",
            "id": "hello-1",  # 毎回ユニークにするのがベスト（例: uuid）
            "method": "tools/call",
            "params": {"name": tool, "arguments": args}
        }
        res = st.session_state.transport.post_jsonrpc(payload)  # ← 202 Accepted が正常
        st.info(f"POST {res.status_code} Accepted（結果はSSEに流れてきます）")       
    except Exception as e:
        st.error(f"Error: {e}")
