# -*- coding: utf-8 -*-
"""Хранилище состояния агента: несколько независимых чатов.

Каждый чат несёт свою историю, факты, ветки, трёхслойную память,
профиль персонализации, конфиг модели и состояние машины состояний задачи.
"""

import json
import os
import pathlib
import tempfile
import threading
import time
import uuid

DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
STATE_FILE = DATA_DIR / "chat_history.json"
LOCK = threading.Lock()

DEFAULT_CONFIG = {
    "provider": "ai-public", "model": "openai/gpt-4.1",
    "systemPromptPreset": "assistant", "temperature": 0.7, "topP": 1.0,
    "frequencyPenalty": 0.0, "presencePenalty": 0.0, "maxTokens": 4000,
    "responseFormat": "text", "maxWords": 0, "maxInputChars": 2000,
    "contextMode": "sliding", "keepRecent": 6, "summarizeEvery": 10, "factsUpdateEvery": 1,
}

DEFAULT_PROFILE = {"identity": "", "style": "", "format": "", "constraints": ""}
DEFAULT_TASK_STATE = {"stage": "Ожидание задачи", "step": "", "expectedAction": ""}


def _new_chat(name=None):
    now = time.time()
    return {
        "id": uuid.uuid4().hex[:12],
        "name": name or "Новый чат",
        "topic": "",
        "createdAt": now,
        "updatedAt": now,
        "history": [],
        "facts": [],
        "factsUpTo": 0,
        "branches": {},
        "activeBranch": None,
        "strategy": "sliding",
        "memory": {"working": [], "long_term": []},
        "profile": dict(DEFAULT_PROFILE),
        "config": dict(DEFAULT_CONFIG),
        "taskState": dict(DEFAULT_TASK_STATE),
        "totalPrompt": 0,
        "totalComp": 0,
    }


def _normalize_profile(profile):
    if not isinstance(profile, dict):
        return dict(DEFAULT_PROFILE)
    out = dict(DEFAULT_PROFILE)
    for k in out:
        v = profile.get(k)
        out[k] = v if isinstance(v, str) else ""
    return out


def _normalize_task_state(ts):
    if not isinstance(ts, dict):
        return dict(DEFAULT_TASK_STATE)
    out = dict(DEFAULT_TASK_STATE)
    for k in out:
        v = ts.get(k)
        out[k] = v if isinstance(v, str) else ""
    return out


def _normalize_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _normalize_chat(data):
    base = _new_chat()
    if not isinstance(data, dict):
        return base

    base["id"] = data.get("id") or base["id"]
    name = data.get("name")
    base["name"] = name if isinstance(name, str) and name.strip() else base["name"]
    topic = data.get("topic")
    base["topic"] = topic if isinstance(topic, str) else ""
    base["createdAt"] = data.get("createdAt", base["createdAt"])
    base["updatedAt"] = data.get("updatedAt", base["updatedAt"])
    base["history"] = data.get("history") if isinstance(data.get("history"), list) else []
    base["facts"] = data.get("facts") if isinstance(data.get("facts"), list) else []
    base["factsUpTo"] = _normalize_int(data.get("factsUpTo"), 0)
    base["branches"] = data.get("branches") if isinstance(data.get("branches"), dict) else {}
    base["activeBranch"] = data.get("activeBranch")
    base["strategy"] = data.get("strategy") or "sliding"

    mem = data.get("memory")
    if not isinstance(mem, dict):
        mem = {}
    base["memory"] = {
        "working": mem.get("working") if isinstance(mem.get("working"), list) else [],
        "long_term": mem.get("long_term") if isinstance(mem.get("long_term"), list) else [],
    }

    base["profile"] = _normalize_profile(data.get("profile"))

    cfg = data.get("config")
    merged_cfg = dict(DEFAULT_CONFIG)
    if isinstance(cfg, dict):
        merged_cfg.update(cfg)
    base["config"] = merged_cfg

    base["taskState"] = _normalize_task_state(data.get("taskState"))
    base["totalPrompt"] = _normalize_int(data.get("totalPrompt"), 0)
    base["totalComp"] = _normalize_int(data.get("totalComp"), 0)
    return base


def _empty_root():
    return {"chats": {}, "activeChatId": None}


def _root_with_single_chat(chat):
    return {"chats": {chat["id"]: chat}, "activeChatId": chat["id"]}


def _migrate_legacy(data):
    """Старый формат (один общий чат на всё приложение) -> новый формат с чатами."""
    if isinstance(data, list):
        data = {"history": data}
    chat = _normalize_chat({
        "name": "Чат 1",
        "history": data.get("history"),
        "facts": data.get("facts"),
        "branches": data.get("branches"),
        "activeBranch": data.get("activeBranch"),
        "strategy": data.get("strategy"),
        "memory": data.get("memory"),
        "profile": data.get("profile"),
    })
    return _root_with_single_chat(chat)


def _normalize_root(data):
    if isinstance(data, dict) and isinstance(data.get("chats"), dict):
        chats = {}
        for cid, cdata in data["chats"].items():
            c = _normalize_chat(cdata)
            c["id"] = cid
            chats[cid] = c
        if not chats:
            chat = _new_chat("Чат 1")
            return _root_with_single_chat(chat)
        active = data.get("activeChatId")
        if active not in chats:
            active = next(iter(chats))
        return {"chats": chats, "activeChatId": active}

    if isinstance(data, (dict, list)) and data:
        return _migrate_legacy(data)

    return _root_with_single_chat(_new_chat("Чат 1"))


def _ensure_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_root():
    """Читает состояние. Если файла нет / он битый / это старый формат без chats,
    сразу нормализует и сохраняет результат — иначе повторный load_root() (например,
    из соседнего запроса GET /api/chats/<id>) сгенерировал бы новый случайный id чата."""
    _ensure_dir()
    with LOCK:
        raw = None
        if STATE_FILE.is_file():
            try:
                raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raw = None

    if isinstance(raw, dict) and isinstance(raw.get("chats"), dict) and raw["chats"]:
        return _normalize_root(raw)

    root = _normalize_root(raw) if raw is not None else _root_with_single_chat(_new_chat("Чат 1"))
    return save_root(root)


def save_root(root):
    _ensure_dir()
    payload = _normalize_root(root)
    with LOCK:
        fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, str(STATE_FILE))
        except Exception:
            pathlib.Path(tmp).unlink(missing_ok=True)
            raise
    return payload


def list_chats():
    root = load_root()
    chats = []
    for cid, c in root["chats"].items():
        chats.append({
            "id": cid,
            "name": c["name"],
            "topic": c["topic"],
            "messageCount": len(c["history"]),
            "updatedAt": c["updatedAt"],
            "taskState": c["taskState"],
        })
    chats.sort(key=lambda x: x["updatedAt"], reverse=True)
    return chats, root["activeChatId"]


def get_chat(chat_id):
    root = load_root()
    return root["chats"].get(chat_id)


def create_chat(name=None):
    root = load_root()
    n = len(root["chats"]) + 1
    chat = _new_chat(name or "Чат {}".format(n))
    root["chats"][chat["id"]] = chat
    root["activeChatId"] = chat["id"]
    root = save_root(root)
    return root["chats"][chat["id"]]


def update_chat(chat_id, patch):
    root = load_root()
    chat = root["chats"].get(chat_id)
    if chat is None:
        return None
    for k, v in (patch or {}).items():
        if k == "id":
            continue
        chat[k] = v
    chat["updatedAt"] = time.time()
    chat = _normalize_chat(chat)
    chat["id"] = chat_id
    chat["updatedAt"] = time.time()
    root["chats"][chat_id] = chat
    root = save_root(root)
    return root["chats"][chat_id]


def delete_chat(chat_id):
    root = load_root()
    if chat_id in root["chats"]:
        del root["chats"][chat_id]
    if not root["chats"]:
        chat = _new_chat("Чат 1")
        root["chats"][chat["id"]] = chat
        root["activeChatId"] = chat["id"]
    elif root["activeChatId"] == chat_id:
        root["activeChatId"] = next(iter(root["chats"]))
    root = save_root(root)
    return root["activeChatId"]


def set_active_chat(chat_id):
    root = load_root()
    if chat_id in root["chats"]:
        root["activeChatId"] = chat_id
        root = save_root(root)
    return root["activeChatId"]
