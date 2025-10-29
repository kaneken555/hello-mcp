# client-streamlit/app.py
import json
import os
import uuid
import streamlit as st
from dotenv import load_dotenv
from mcp_client.transport import MCPTransport
from streamlit_autorefresh import st_autorefresh

# ---------- 初期設定 ----------
load_dotenv()
MCP_POST_URL = os.getenv("MCP_POST_URL", "http://localhost:3000/messages")
MCP_SSE_URL  = os.getenv("MCP_SSE_URL",  "http://localhost:3000/sse")

st.set_page_config(page_title="Hello MCP Client (SSE)", page_icon="🧩", layout="wide")
st.title("🧩 Hello MCP – Streamlit Client (with SSE)")

# ---------- セッション状態 ----------
if "transport" not in st.session_state:
    st.session_state.transport = MCPTransport(MCP_SSE_URL, MCP_POST_URL)

# 非同期用の受信バッファ: req_id -> [payload, ...]
if "async_payloads" not in st.session_state:
    st.session_state.async_payloads = {}
# 非同期の進捗を見やすくするため最新のreq_idを覚えておく
if "last_async_id" not in st.session_state:
    st.session_state.last_async_id = None

# ---------- ユーティリティ ----------
def ensure_connected_and_ready() -> bool:
    t = st.session_state.transport
    if not t.is_connected():
        st.warning("SSEが未接続です。先に『Connect SSE』を押してください。")
        return False
    if not t.wait_until_ready():
        st.warning("SSEのendpointが未確立です。もう一度お試しください。")
        return False
    return True

# ---------- 接続操作 ----------
with st.expander("MCP Settings", expanded=False):
    st.write("POST:", MCP_POST_URL)
    st.write("SSE :", MCP_SSE_URL)

colA, colB, colC, colD = st.columns([1,1,1,2])
with colA:
    if st.button("🔌 Connect SSE"):
        ok = st.session_state.transport.connect_sse()
        st.success("SSE connected." if ok else "SSE connect failed.")
with colB:
    if st.button("🔄 Reconnect"):
        st.session_state.transport.disconnect_sse()
        st.session_state.transport.connect_sse()
        st.info("Reconnected.")
with colC:
    if st.button("🔴 Disconnect SSE"):
        st.session_state.transport.disconnect_sse()
        st.warning("SSE disconnected.")
with colD:
    status = "✅ Connected" if st.session_state.transport.is_connected() else "⚠️ Not connected"
    st.caption(f"SSE Status: {status}")

with st.expander("Last SSE Event (debug)", expanded=False):
    st.json(st.session_state.transport.last_event())

st.divider()

# ---------- ツール入力 ----------
tool = st.text_input("Tool name", value="say_hello")
params_text = st.text_area("Parameters (JSON)", value='{"name": "Ken"}', height=120)

# ---------- 同期版：1クリックで完了 ----------
st.subheader("🟢 Sync call（1クリックで完了・推奨）")
if st.button("Call Tool (sync)"):
    if ensure_connected_and_ready():
        try:
            args = json.loads(params_text or "{}")
        except Exception as e:
            st.error(f"Parameters JSONエラー: {e}")
        else:
            with st.status("実行中…（SSE応答待ち）", expanded=False) as s:
                result, err = st.session_state.transport.call_tool(
                    name=tool, arguments=args, timeout_sec=20.0
                )
                if err:
                    s.update(label="エラー", state="error")
                    st.error(err)
                else:
                    s.update(label="完了", state="complete")
                    st.json(result)

st.divider()

# ---------- 非同期版：開始→進捗→完了 ----------
st.subheader("🟡 Async call（開始→進捗→完了 / 長時間処理向け）")
col1, col2 = st.columns([1, 3])

with col1:
    if st.button("Call Tool (async)"):
        if ensure_connected_and_ready():
            args = json.loads(params_text or "{}")
            req_id = f"req-{uuid.uuid4().hex[:8]}"
            st.session_state.last_async_id = req_id
            st.session_state.async_payloads[req_id] = []

            def _cb(payload, _req_id=req_id):
                # 背景スレッド: payload をためるだけ（UIは触らない）
                st.session_state.async_payloads.setdefault(_req_id, []).append(payload)
                # 受信カウンタを更新しておくと、外側で表示にも使える
                st.session_state[f"async_count_{_req_id}"] = len(st.session_state.async_payloads[_req_id])

            st.session_state.transport.on_message(req_id, _cb)
            st.info(f"非同期リクエスト開始: {req_id}")
            st.session_state.transport.call_tool_async(tool, args, request_id=req_id)

with col2:
    # 表示対象IDの選択
    req_id = st.session_state.last_async_id
    st.write("表示対象 request_id:", req_id or "（未実行）")
    target_id = st.text_input("表示する request_id を変更（任意）", value=req_id or "")
    if target_id:
        req_id = target_id

    # ここを「payloadsが空でもUIが出る」ように微修正
    if req_id and req_id in st.session_state.async_payloads:
        payloads = st.session_state.async_payloads[req_id]
        st.caption(f"受信数: {len(payloads)}")
        if payloads:
            for i, p in enumerate(payloads):
                with st.expander(f"#{i+1} payload", expanded=(i == len(payloads)-1)):
                    st.json(p)
        else:
            st.caption("（まだ受信はありません。到着すると自動/手動リフレッシュで表示されます）")

        # Stopは常に表示（payload有無に依らない）
        if st.button("Stop listen (off_message)"):
            st.session_state.transport.off_message(req_id)
            st.success(f"Stopped listening: {req_id}")
    else:
        st.caption("受信結果はここに流れてきます（progress/result が届くたびに増えます）。")