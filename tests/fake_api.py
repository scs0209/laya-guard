"""A stand-in for the Anthropic, OpenAI and Gemini APIs on 127.0.0.1: it records every request and answers from a script,
so the API adapters are tested over real HTTP without a real key or a real bill.

`start()` returns a FakeApi; point an adapter's `base` at `fake.base`. `/models` answers with `fake.models`, a chat call
(`/messages` in Anthropic's shape, `/chat/completions` in OpenAI's) with the next of `fake.replies` (the last one repeats).
`status`/`error` make every call fail, `sleep` makes it slow, `raw` sends a body that isn't JSON.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeApi:
    def __init__(self):
        self.requests: list[dict] = []
        self.models: list[dict] = []
        self.replies: list = []
        self.status, self.error, self.sleep, self.raw = 200, None, 0.0, None
        self._served = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                fake._handle(self, None)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                fake._handle(self, json.loads(self.rfile.read(length) or b"null"))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def chats(self) -> list[dict]:
        return [r for r in self.requests if r["method"] == "POST"]

    def _next_text(self) -> str:
        reply = self.replies[min(self._served, len(self.replies) - 1)] if self.replies else {}
        self._served += 1
        return reply if isinstance(reply, str) else json.dumps(reply)

    def _handle(self, h: BaseHTTPRequestHandler, body):
        self.requests.append({"method": h.command, "path": h.path, "headers": {k.lower(): v for k, v in h.headers.items()}, "body": body})
        if self.sleep:
            time.sleep(self.sleep)
        if self.status != 200:
            return self._send(h, self.status, json.dumps(self.error) if self.error is not None else "")
        if self.raw is not None:
            return self._send(h, 200, self.raw)
        if h.command == "GET":
            return self._send(h, 200, json.dumps({"data": self.models}))
        text = self._next_text()
        if h.path.endswith("/messages"):
            out = {"type": "message", "model": body.get("model"), "content": [{"type": "text", "text": text}],
                   "usage": {"input_tokens": 10, "output_tokens": 5}}
        else:
            out = {"model": body.get("model"), "choices": [{"index": 0, "message": {"role": "assistant", "content": text}}],
                   "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        self._send(h, 200, json.dumps(out))

    def _send(self, h, status: int, text: str):
        data = text.encode("utf-8")
        try:
            h.send_response(status)
            h.send_header("Content-Type", "application/json")
            h.send_header("Content-Length", str(len(data)))
            h.end_headers()
            h.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def start() -> FakeApi:
    return FakeApi()
