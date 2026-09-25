# -*- coding: utf-8 -*-
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
from mcp_bridge import list_tools as list_mcp_tools
from store import (
    list_chats, get_chat, create_chat, update_chat, delete_chat, set_active_chat,
    list_profile_presets, add_custom_preset, delete_custom_preset, migrate_legacy_presets,
    transition_chat_stage,
)
from task_states import STAGES, ALLOWED_TRANSITIONS, STAGE_DESCRIPTIONS

STATIC_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_BODY_BYTES = 512 * 1024
CHATS_PREFIX = "/api/chats/"


class Server(ThreadingHTTPServer):
    # На Windows SO_REUSEADDR (в отличие от Linux) разрешает второму процессу
    # бесшумно забиндиться на уже занятый порт — оба живут одновременно, и ОС
    # непредсказуемо раздаёт им входящие соединения. Отключаем, чтобы повторный
    # запуск падал с понятной "Address already in use" вместо дублей процессов.
    allow_reuse_address = False


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
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/config":
            models_catalog = {}
            for p in PROVIDERS:
                models_catalog[p] = get_models(p)
            self._send_json(200, {"catalog": models_catalog})
            return

        if path == "/api/mcp/tools":
            try:
                info = list_mcp_tools()
                self._send_json(200, {"ok": True, **info})
            except Exception as e:
                self._send_json(200, {"ok": False, "error": str(e)})
            return

        if path == "/api/chats":
            chats, active_id = list_chats()
            self._send_json(200, {"chats": chats, "activeChatId": active_id})
            return

        if path == "/api/profile-presets":
            self._send_json(200, {"presets": list_profile_presets()})
            return

        if path == "/api/task-states":
            self._send_json(200, {
                "stages": STAGES,
                "transitions": ALLOWED_TRANSITIONS,
                "descriptions": STAGE_DESCRIPTIONS,
            })
            return

        if path.startswith(CHATS_PREFIX):
            chat_id = path[len(CHATS_PREFIX):]
            chat = get_chat(chat_id)
            if chat is None:
                self._send_json(404, {"error": "Чат не найден"})
                return
            self._send_json(200, {"chat": chat})
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
        self._send(200, file_path.read_bytes(), content_type or "application/octet-stream")

    def do_POST(self):
        path = self.path.split("?", 1)[0]

        if path == "/api/chats":
            data = self._read_json()
            chat = create_chat(data.get("name"))
            self._send_json(200, {"chat": chat})
            return

        if path == "/api/profile-presets":
            data = self._read_json()
            preset = add_custom_preset(data.get("name", ""), data.get("profile", {}))
            if preset is None:
                self._send_json(400, {"error": "Укажите название пресета"})
                return
            self._send_json(200, {"preset": preset, "presets": list_profile_presets()})
            return

        if path.startswith(CHATS_PREFIX) and path.endswith("/active"):
            chat_id = path[len(CHATS_PREFIX):-len("/active")]
            active_id = set_active_chat(chat_id)
            self._send_json(200, {"activeChatId": active_id})
            return

        if path.startswith(CHATS_PREFIX) and path.endswith("/transition"):
            chat_id = path[len(CHATS_PREFIX):-len("/transition")]
            data = self._read_json()
            result = transition_chat_stage(chat_id, data.get("to", ""), source="manual")
            if result is None:
                self._send_json(404, {"error": "Чат не найден"})
                return
            code = 200 if result["ok"] else 409
            self._send_json(code, {
                "ok": result["ok"], "message": result["message"],
                "entry": result["entry"], "chat": result["chat"],
            })
            return

        if path.startswith(CHATS_PREFIX):
            chat_id = path[len(CHATS_PREFIX):]
            data = self._read_json()
            chat = update_chat(chat_id, data)
            if chat is None:
                self._send_json(404, {"error": "Чат не найден"})
                return
            self._send_json(200, {"chat": chat})
            return

        if path != "/api/run":
            self._send_json(404, {"error": "Неизвестный endpoint"})
            return

        req_data = self._read_json()
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
        path = self.path.split("?", 1)[0]
        if path.startswith(CHATS_PREFIX):
            chat_id = path[len(CHATS_PREFIX):]
            active_id = delete_chat(chat_id)
            self._send_json(200, {"activeChatId": active_id})
            return
        if path.startswith("/api/profile-presets/"):
            preset_id = path[len("/api/profile-presets/"):]
            delete_custom_preset(preset_id)
            self._send_json(200, {"presets": list_profile_presets()})
            return
        self._send_json(404, {"error": "Неизвестный endpoint"})

    def log_message(self, fmt, *args):
        pass


def main():
    try:
        get_api_key("ai-public")
        get_api_key("nvidia")
    except Exception as e:
        print("Предупреждение: {}".format(e))
    migrate_legacy_presets()
    server = Server(("127.0.0.1", 5182), Handler)
    print("Сервер запущен: http://127.0.0.1:5182")
    if not os.getenv("TF_NO_BROWSER"):
        threading.Timer(0.7, lambda: webbrowser.open("http://127.0.0.1:5182")).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
