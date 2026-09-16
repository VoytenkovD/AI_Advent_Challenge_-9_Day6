# -*- coding: utf-8 -*-
"""Хранилище полного состояния агента с моделью памяти."""

import json
import os
import pathlib
import tempfile
import threading

DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
STATE_FILE = DATA_DIR / "chat_history.json"
LOCK = threading.Lock()

_EMPTY = {
    "history": [],
    "facts": [],
    "branches": {},
    "activeBranch": None,
    "strategy": "sliding",
    "memory": {"working": [], "long_term": []},
}


def _ensure_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _normalize(data):
    if isinstance(data, list):
        return {"history": data, "facts": [], "branches": {}, "activeBranch": None, "strategy": "sliding", "memory": {"working": [], "long_term": []}}
    if isinstance(data, dict):
        history = data.get("history") if isinstance(data.get("history"), list) else []
        mem = data.get("memory")
        if not isinstance(mem, dict):
            mem = {"working": [], "long_term": []}
        return {
            "history": history,
            "facts": data.get("facts") if isinstance(data.get("facts"), list) else [],
            "branches": data.get("branches") if isinstance(data.get("branches"), dict) else {},
            "activeBranch": data.get("activeBranch"),
            "strategy": data.get("strategy", "sliding"),
            "memory": mem,
        }
    return dict(_EMPTY)


def load_state():
    _ensure_dir()
    with LOCK:
        if not STATE_FILE.is_file():
            return dict(_EMPTY)
        try:
            return _normalize(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            return dict(_EMPTY)


def save_state(state):
    _ensure_dir()
    payload = {
        "history": state.get("history") or [],
        "facts": state.get("facts") or [],
        "branches": state.get("branches") or {},
        "activeBranch": state.get("activeBranch"),
        "strategy": state.get("strategy", "sliding"),
        "memory": state.get("memory") or {"working": [], "long_term": []},
    }
    with LOCK:
        fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, str(STATE_FILE))
        except Exception:
            pathlib.Path(tmp).unlink(missing_ok=True)
            raise


def clear():
    _ensure_dir()
    with LOCK:
        STATE_FILE.unlink(missing_ok=True)