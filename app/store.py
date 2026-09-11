# -*- coding: utf-8 -*-
"""Хранилище состояния диалога: один JSON-файл с атомарной записью.

Состояние: история сообщений, резюме старой части диалога и указатель
summaryUpTo — сколько сообщений истории уже вошло в резюме.

Пишем во временный файл и переименовываем — при падении сервера посреди
записи на диске остаётся предыдущая целая версия.
"""
import json
import os
import pathlib
import tempfile
import threading

DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
STATE_FILE = DATA_DIR / "chat_history.json"
LOCK = threading.Lock()

# Файл старого формата назывался так же, но содержал голый список.
_EMPTY = {"history": [], "summary": "", "summaryUpTo": 0}


def _ensure_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _normalize(data):
    """Приводит данные любого формата к состоянию."""
    if isinstance(data, list):
        return {"history": data, "summary": "", "summaryUpTo": 0}
    if isinstance(data, dict):
        history = data.get("history") or []
        if not isinstance(history, list):
            history = []
        return {
            "history": history,
            "summary": data.get("summary") or "",
            "summaryUpTo": int(data.get("summaryUpTo") or 0),
        }
    return dict(_EMPTY)


def load_state():
    """Возвращает состояние диалога."""
    _ensure_dir()
    with LOCK:
        if not STATE_FILE.is_file():
            return dict(_EMPTY)
        try:
            return _normalize(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, ValueError):
            return dict(_EMPTY)


def save_state(state):
    """Атомарно записывает состояние в файл."""
    _ensure_dir()
    payload = {
        "history": state.get("history") or [],
        "summary": state.get("summary") or "",
        "summaryUpTo": int(state.get("summaryUpTo") or 0),
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
    """Удаляет файл состояния."""
    _ensure_dir()
    with LOCK:
        STATE_FILE.unlink(missing_ok=True)