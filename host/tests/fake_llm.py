"""A local stand-in for the chat completions endpoint. No test calls a real API."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def reply(content):
    """A 200 response whose message text is content."""
    return 200, {}, json.dumps({"choices": [{"message": {"content": content}}]}).encode()


class FakeLlm:
    """Serves a script of (status, headers, body) replies, repeating the last one.

    A reply of the form ("hang", seconds) sleeps past the client's timeout.
    """

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                index = min(len(outer.requests), len(outer.script) - 1)
                outer.requests.append((self.path, dict(self.headers), body))
                step = outer.script[index]
                if step[0] == "hang":
                    time.sleep(step[1])
                    return
                status, headers, payload = step
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
