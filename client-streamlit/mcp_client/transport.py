import threading
import time
import json
import uuid
import requests
from sseclient import SSEClient
from urllib.parse import urlparse, urljoin
from typing import Callable, Dict, Optional, Any

SSE_EVENT_ENDPOINT = "endpoint"
SSE_EVENT_MESSAGE = "message"
SSE_EVENT_PROGRESS = "progress"  # ← 進捗イベントを拡張運用する場合に利用（任意）

class MCPTransport:
    """
    - connect_sse() / disconnect_sse()
    - is_connected(), is_ready(), wait_until_ready()
    - call_tool(name, arguments, timeout_sec)  ← 同期（POST→SSE応答をidで待つ）
    - call_tool_async(name, arguments) -> request_id  ← 非同期（POSTだけして戻す）
    - on_message(request_id, callback) / off_message(request_id)
    - cancel(request_id)  ← 簡易キャンセル（ベストはサーバ側が対応していること）
    """

    def __init__(self, sse_url: str, post_url: str, timeout: int = 30):
        self.sse_url = sse_url
        self.post_url = post_url
        self.timeout = timeout

        self._session = requests.Session()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        self._connected = False          # SSE TCP確立
        self._ready = False              # endpoint 受信後
        self._resolved_post_url: Optional[str] = None

        self._last_event: Optional[Dict[str, Any]] = None

        # 同期待ち用: id -> {"event": Event, "result":..., "error":...}
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

        # 非同期コールバック: id -> callback(payload_dict)
        self._callbacks: Dict[str, Callable[[Dict[str, Any]], None]] = {}

        # 簡易キャンセル管理（クライアント側のみ。実運用はサーバのcancel対応が必要）
        self._canceled: set[str] = set()

    # ---------- 基本ユーティリティ ----------

    def _resolve_post_url(self, endpoint_path: str) -> str:
        o = urlparse(self.sse_url)
        origin = f"{o.scheme}://{o.netloc}"
        return urljoin(origin, endpoint_path)

    def connect_sse(self) -> bool:
        if self._connected:
            return True
        self._stop.clear()
        self._ready = False
        self._resolved_post_url = None

        def _run():
            try:
                with self._session.get(self.sse_url, stream=True, timeout=self.timeout) as r:
                    r.raise_for_status()
                    client = SSEClient(r)
                    self._connected = True
                    for ev in client.events():
                        if self._stop.is_set():
                            break

                        if ev.event == SSE_EVENT_ENDPOINT and ev.data:
                            self._resolved_post_url = self._resolve_post_url(ev.data.strip())
                            self._ready = True

                        elif ev.event in (SSE_EVENT_MESSAGE, SSE_EVENT_PROGRESS) and ev.data:
                            try:
                                payload = json.loads(ev.data)
                                rpc_id = payload.get("id")

                                # --- キャンセル済みなら破棄 ---
                                if rpc_id and rpc_id in self._canceled:
                                    continue

                                # 1) 同期待ちの人へ
                                with self._lock:
                                    waiter = self._pending.get(rpc_id)
                                    if waiter:
                                        if "error" in payload:
                                            waiter["error"] = payload["error"]
                                        else:
                                            # message or progress いずれも result/params などをそのまま格納
                                            waiter["result"] = payload.get("result") or payload
                                        waiter["event"].set()

                                # 2) 非同期コールバックへ
                                cb = self._callbacks.get(rpc_id)
                                if cb:
                                    try:
                                        cb(payload)
                                    except Exception:
                                        pass

                            except Exception:
                                pass

                        self._last_event = {"event": ev.event, "data": ev.data}
            finally:
                # 接続が切れたら全待機者へ通知＆初期化
                self._connected = False
                self._ready = False
                self._resolved_post_url = None
                with self._lock:
                    for entry in self._pending.values():
                        entry["error"] = {"code": -1, "message": "SSE disconnected"}
                        entry["event"].set()
                    self._pending.clear()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        return True

    def disconnect_sse(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None
        self._connected = False
        self._ready = False
        self._resolved_post_url = None

    def is_connected(self) -> bool:
        return self._connected

    def is_ready(self) -> bool:
        return self._ready and bool(self._resolved_post_url)

    def wait_until_ready(self, tries: int = 40, sleep_sec: float = 0.1) -> bool:
        for _ in range(tries):
            if self.is_ready():
                return True
            time.sleep(sleep_sec)
        return False

    def last_event(self):
        return self._last_event

    # ---------- 低レベル送信 ----------

    def _post_jsonrpc(self, payload: dict) -> requests.Response:
        url = self._resolved_post_url or self.post_url
        return self._session.post(url, json=payload, timeout=self.timeout)

    # ---------- 同期（待つ）API ----------

    def _enqueue_and_wait(self, payload: dict, timeout_sec: float = 20.0):
        rpc_id = payload.get("id") or str(uuid.uuid4())
        payload["id"] = rpc_id

        waiter = {"event": threading.Event(), "result": None, "error": None}
        with self._lock:
            self._pending[rpc_id] = waiter

        res = self._post_jsonrpc(payload)  # 202 Accepted が正常
        # HTTP本文は使わない（SSEで来る）

        ok = waiter["event"].wait(timeout=timeout_sec)
        with self._lock:
            self._pending.pop(rpc_id, None)

        if not ok:
            return None, {"code": -2, "message": "timeout waiting SSE message", "http_status": getattr(res, "status_code", None)}
        if waiter["error"] is not None:
            return None, waiter["error"]
        return waiter["result"], None

    def call_tool(self, name: str, arguments: dict, timeout_sec: float = 20.0):
        """1クリックで完結（推奨）：POST→SSE応答（同id）を待って返す"""
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        return self._enqueue_and_wait(payload, timeout_sec=timeout_sec)

    # ---------- 非同期（開始だけする）API ----------

    def call_tool_async(self, name: str, arguments: dict, request_id: Optional[str] = None) -> str:
        """POSTだけして戻る。結果は SSE の message で流れる"""
        rpc_id = request_id or str(uuid.uuid4())
        payload = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        self._post_jsonrpc(payload)  # 202が正常
        return rpc_id

    def on_message(self, request_id: str, callback: Callable[[Dict[str, Any]], None]):
        """message/progress 受信時に都度コールバック。完了後に off_message() を呼ぶ運用に"""
        self._callbacks[request_id] = callback

    def off_message(self, request_id: str):
        self._callbacks.pop(request_id, None)

    def cancel(self, request_id: str):
        """簡易キャンセル（クライアント側フィルタ）。本当の中断はサーバ側のcancel実装が必要"""
        self._canceled.add(request_id)
        # サーバ側に cancel を通知できるなら、以下のようなRPCも検討：
        # payload = {"jsonrpc":"2.0","id": str(uuid.uuid4()),"method":"jobs/cancel","params":{"id": request_id}}
        # self._post_jsonrpc(payload)
