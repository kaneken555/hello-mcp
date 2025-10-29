# client-streamlit/mcp_client/transport.py
import threading
import requests
import time
from sseclient import SSEClient
from urllib.parse import urlparse, urljoin

class MCPTransport:
    def __init__(self, sse_url: str, post_url: str, timeout: int = 30):
        self.sse_url = sse_url
        self.post_url = post_url
        self.timeout = timeout
        self._thread = None
        self._stop = threading.Event()
        self._session = requests.Session()
        self._connected = False               # SSEのTCP接続確立フラグ
        self._ready = False                   # endpoint 受信後フラグ（←重要）
        self._last_event = None
        self._resolved_post_url = None        # 実際にPOSTするURL（endpoint反映後）

    def _resolve_post_url(self, endpoint_path: str):
        # sse_urlのオリジンを使って絶対URLを作る
        o = urlparse(self.sse_url)
        origin = f"{o.scheme}://{o.netloc}"
        # data が '/messages?sessionId=...' のような絶対パス想定
        return urljoin(origin, endpoint_path)

    def connect_sse(self):
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
                        # 最初に飛んでくる endpoint イベントを捕まえる
                        if ev.event == "endpoint" and ev.data:
                            self._resolved_post_url = self._resolve_post_url(ev.data.strip())
                            self._ready = True
                        self._last_event = {"event": ev.event, "data": ev.data}
            except Exception as e:
                self._last_event = {"error": str(e)}
            finally:
                self._connected = False
                self._ready = False
                self._resolved_post_url = None

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
        """endpoint を受け取って実際にPOSTできる状態か"""
        return self._ready and bool(self._resolved_post_url)

    def post_jsonrpc(self, payload: dict) -> requests.Response:
        """ endpoint で通知されたURLにPOST（なければ従来のpost_urlにフォールバック） """
        url = self._resolved_post_url or self.post_url
        return self._session.post(url, json=payload, timeout=self.timeout)

    def wait_until_ready(self, tries: int = 20, sleep_sec: float = 0.15) -> bool:
        # endpoint 受信を待つ（tools/list を叩く必要はもうない）
        for _ in range(tries):
            if self.is_ready():
                return True
            time.sleep(sleep_sec)
        return False

    def last_event(self):
        return self._last_event