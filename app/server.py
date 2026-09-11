# -*- coding: utf-8 -*-
import argparse
import json
import mimetypes
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm import LlmError, get_api_key, get_models, PROVIDERS
from agent import run_agent
from store import load_state, save_state, clear

STATIC_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_BODY_BYTES = 512 * 1024


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, content_type="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _send_json(self, code, payload):
        self._send(
            code, json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/config":
            models_catalog = {}
            for p in PROVIDERS:
                models_catalog[p] = get_models(p)
            self._send_json(200, {"catalog": models_catalog})
            return

        if path == "/api/history":
            state = load_state()
            self._send_json(
                200,
                {
                    "history": state["history"],
                    "summary": state["summary"],
                    "summaryUpTo": state["summaryUpTo"],
                },
            )
            return

        if path in ("/", "/index.html"):
            self._serve_file(STATIC_DIR / "index.html")
            return

        candidate = (STATIC_DIR / path.lstrip("/")).resolve()
        if not str(candidate).startswith(str(STATIC_DIR.resolve())):
            self._send_json(403, {"error": "Доступ запрещён"})
            return
        self._serve_file(candidate)

    def _serve_file(self, file_path):
        if not file_path.is_file():
            self._send_json(404, {"error": "Не найдено"})
            return
        content_type, _ = mimetypes.guess_type(str(file_path))
        if content_type in ("text/html", "text/css", "application/javascript"):
            content_type += "; charset=utf-8"
        self._send(
            200,
            file_path.read_bytes(),
            content_type or "application/octet-stream",
        )

    def do_POST(self):
        path = self.path.split("?", 1)[0]

        if path == "/api/history":
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            save_state(
                {
                    "history": data.get("history", []),
                    "summary": data.get("summary") or "",
                    "summaryUpTo": int(data.get("summaryUpTo") or 0),
                }
            )
            self._send_json(200, {"saved": True})
            return

        if path == "/api/history/summary":
            state = load_state()
            state["summary"] = ""
            state["summaryUpTo"] = 0
            save_state(state)
            self._send_json(200, {"cleared": True})
            return

        if path != "/api/run":
            self._send_json(404, {"error": "Неизвестный endpoint"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        req_data = json.loads(self.rfile.read(length).decode("utf-8"))

        question = req_data.get("question", "").strip()
        agent_data = req_data.get("agent", {})

        try:
            result = {"status": "done", **run_agent(question, agent_data)}
        except LlmError as e:
            result = {"status": "error", "error": str(e)}
        except Exception as e:
            result = {"status": "error", "error": str(e)}

        self._send_json(200, {"result": result})

    def do_DELETE(self):
        if self.path.split("?", 1)[0] == "/api/history":
            clear()
            self._send_json(200, {"cleared": True})
        else:
            self._send_json(404, {"error": "Неизвестный endpoint"})

    def log_message(self, fmt, *args):
        pass


def main():
    try:
        get_api_key("ai-public")
        get_api_key("nvidia")
    except Exception as e:
        print("Предупреждение: {}".format(e))

    server = ThreadingHTTPServer(("127.0.0.1", 5182), Handler)
    print("Сервер запущен: http://127.0.0.1:5182")
    if not os.getenv("TF_NO_BROWSER"):
        threading.Timer(
            0.7, lambda: webbrowser.open("http://127.0.0.1:5182")
        ).start()
    server.serve_forever()


if __name__ == "__main__":
    main()