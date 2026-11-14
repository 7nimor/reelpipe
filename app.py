import json
import socket
import threading
import urllib.error
import urllib.request
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from pipeline import run_pipeline
from status_board import board
from upload_latest import save_sessionid

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}/"

state_lock = threading.Lock()
challenge_event = threading.Event()
state = {
    "running": False,
    "logs": [],
    "error": None,
    "result": None,
    "challenge": {
        "needed": False,
        "username": "",
        "choice": "",
    },
    "challenge_code": None,
}


def emit(message, level="info"):
    with state_lock:
        state["logs"].append({"msg": str(message), "level": level})


def reset_challenge():
    challenge_event.clear()
    with state_lock:
        state["challenge"] = {
            "needed": False,
            "username": "",
            "choice": "",
        }
        state["challenge_code"] = None


def challenge_handler(username, choice):
    with state_lock:
        state["challenge"] = {
            "needed": True,
            "username": username or "",
            "choice": str(choice),
        }
        state["challenge_code"] = None
    challenge_event.clear()
    emit(f"challenge: code needed for {username}")
    if not challenge_event.wait(timeout=300):
        reset_challenge()
        raise RuntimeError("challenge timeout")
    with state_lock:
        code = state["challenge_code"]
        state["challenge"] = {
            "needed": False,
            "username": "",
            "choice": "",
        }
        state["challenge_code"] = None
    if not code:
        raise RuntimeError("challenge code empty")
    emit("challenge: code submitted")
    return code


def worker():
    reset_challenge()
    try:
        result = run_pipeline(emit=emit, challenge_handler=challenge_handler)
        with state_lock:
            state["result"] = result
            state["error"] = None
    except Exception as error:
        emit(f"Error: {error}", level="error")
        with state_lock:
            state["error"] = str(error)
            state["result"] = None
    finally:
        reset_challenge()
        with state_lock:
            state["running"] = False


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, format, *args):
        return

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/logs":
            with state_lock:
                payload = {
                    "running": state["running"],
                    "logs": list(state["logs"]),
                    "error": state["error"],
                    "result": state["result"],
                    "challenge": dict(state["challenge"]),
                }
            self._json(200, payload)
            return
        if path == "/api/status":
            self._json(200, board())
            return
        if path in ("/", "/index.html"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/challenge":
            data = self._read_json()
            code = str(data.get("code") or "").strip()
            if not code:
                self._json(400, {"error": "code required"})
                return
            with state_lock:
                if not state["challenge"]["needed"]:
                    self._json(409, {"error": "no challenge pending"})
                    return
                state["challenge_code"] = code
            challenge_event.set()
            self._json(200, {"ok": True})
            return
        if path == "/api/sessionid":
            data = self._read_json()
            sessionid = str(data.get("sessionid") or "").strip()
            try:
                save_sessionid(sessionid)
            except Exception as error:
                self._json(400, {"error": str(error)})
                return
            self._json(200, {"ok": True})
            return
        if path != "/api/run":
            self._json(404, {"error": "not found"})
            return
        with state_lock:
            if state["running"]:
                self._json(409, {"error": "already running"})
                return
            state["running"] = True
            state["logs"] = []
            state["error"] = None
            state["result"] = None
        threading.Thread(target=worker, daemon=True).start()
        self._json(200, {"ok": True})


def already_running():
    try:
        with urllib.request.urlopen(URL, timeout=1) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return False


def port_busy():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        return sock.connect_ex((HOST, PORT)) == 0
    finally:
        sock.close()


def main():
    if already_running() or port_busy():
        print(f"reelpipe already running: {URL}")
        webbrowser.open(URL)
        return
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"reelpipe: {URL}")
    threading.Timer(0.4, lambda: webbrowser.open(URL)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
