# -*- coding: utf-8 -*-
"""Хранилище истории диалога: один JSON-файл с атомарной записью.

Пишем во временный файл и переименовываем — при падении сервера посреди
записи на диске остаётся предыдущая целая версия.
"""
import json
import os
import pathlib
import tempfile
import threading

DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
HISTORY_FILE = DATA_DIR / "chat_history.json"
LOCK = threading.Lock()


def _ensure_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load():
    """Возвращает сохранённую историю или пустой список."""
    _ensure_dir()
    with LOCK:
        if not HISTORY_FILE.is_file():
            return []
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []


def save(history):
    """Атомарно записывает историю в файл."""
    _ensure_dir()
    with LOCK:
        fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False)
            os.replace(tmp, str(HISTORY_FILE))
        except Exception:
            pathlib.Path(tmp).unlink(missing_ok=True)
            raise


def clear():
    """Удаляет файл истории."""
    _ensure_dir()
    with LOCK:
        HISTORY_FILE.unlink(missing_ok=True)