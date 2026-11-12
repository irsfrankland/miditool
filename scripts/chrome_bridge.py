"""Talk to the Songsterr page in Chrome through the extension in chrome-extension/.

The extension posts the tab player's state (playing, measure, when the
measure began) to this computer only, at 127.0.0.1. The reply can carry one
command for the page (play or pause).
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 47391


class ChromeBridge:
    def __init__(self, port: int = PORT) -> None:
        self.port = port
        self.lock = threading.Lock()
        self.state: dict | None = None
        self.received = 0.0
        self.command: str | None = None
        self.error = ""
        self.server: ThreadingHTTPServer | None = None
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def _headers(self, code: int = 200) -> None:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.end_headers()

            def do_OPTIONS(self) -> None:  # noqa: N802
                self._headers(204)

            def do_POST(self) -> None:  # noqa: N802
                if self.path.rstrip("/") != "/songsterr":
                    self._headers(404)
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    state = json.loads(self.rfile.read(min(length, 65536)) or b"{}")
                except (ValueError, json.JSONDecodeError):
                    self._headers(400)
                    return
                command = bridge.receive(state if isinstance(state, dict) else {})
                self._headers()
                self.wfile.write(json.dumps({"command": command}).encode())

            def log_message(self, *_args) -> None:
                pass

        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError as exc:
            self.error = f"Port {port} is busy ({exc.strerror or exc}). Is another finder open?"
            return
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def receive(self, state: dict) -> str | None:
        with self.lock:
            self.state = state
            self.received = time.time()
            command, self.command = self.command, None
        return command

    def latest(self) -> tuple[dict | None, float]:
        """The last state from Chrome and how many seconds ago it came."""
        with self.lock:
            if self.state is None:
                return None, float("inf")
            return dict(self.state), time.time() - self.received

    def send(self, command: str) -> None:
        with self.lock:
            self.command = command

    def close(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
