# -*- coding: utf-8 -*-
"""Хранилище состояния агента: несколько независимых чатов + отдельный файл пресетов профиля.

Каждый чат несёт свою историю, факты, ветки, трёхслойную память,
профиль персонализации, конфиг модели и состояние машины состояний задачи.
Пользовательские пресеты профиля живут отдельно от чатов, в своём файле,
и общие для всех чатов.
"""

import json
import os
import pathlib
import tempfile
import threading
import time
import uuid

from task_states import (
    STAGES, STAGE_WAITING, STAGE_DESCRIPTIONS, DEFAULT_TASK_STATE as FSM_DEFAULT_TASK_STATE,
    transition as fsm_transition,
)

DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
STATE_FILE = DATA_DIR / "chat_history.json"
PRESETS_FILE = DATA_DIR / "presets.json"
CHATS_LOCK = threading.Lock()
PRESETS_LOCK = threading.Lock()

DEFAULT_CONFIG = {
    "provider": "ai-public", "model": "openai/gpt-4.1",
    "systemPromptPreset": "assistant", "temperature": 0.7, "topP": 1.0,
    "frequencyPenalty": 0.0, "presencePenalty": 0.0, "maxTokens": 4000,
    "responseFormat": "text", "maxWords": 0, "maxInputChars": 2000,
    "contextMode": "sliding", "keepRecent": 6, "summarizeEvery": 10, "factsUpdateEvery": 1,
    "mcpEnabled": False,
}

DEFAULT_PROFILE = {"identity": "", "style": "", "format": "", "constraints": ""}
DEFAULT_TASK_STATE = dict(FSM_DEFAULT_TASK_STATE)

PROFILE_PRESETS = [
    {
        "id": "student-1course",
        "name": "Студент политеха, 1 курс",
        "profile": {
            "identity": "Студент политехнического института, 1 курс. Своих конспектов лекций нет.",
            "style": "Точные формулировки, без общих слов и лишней воды.",
            "format": "Ответы по пунктам (нумерованный список).",
            "constraints": (
                "Нет конспектов лекций и записей с занятий — не отсылай к «как было на лекции» "
                "или «как в конспекте», объясняй материал с нуля, как для человека без базы."
            ),
        },
    },
    {
        "id": "future-hunter",
        "name": "Будущий охотник",
        "profile": {
            "identity": "Мужчина, хочет стать охотником. Практического опыта в охоте нет.",
            "style": "Точные формулировки.",
            "format": "Ответы по пунктам (нумерованный список).",
            "constraints": (
                "Нет никаких охотничьих принадлежностей — ружья, разрешения, сейфа, снаряжения. "
                "Не предполагай, что что-то из этого уже есть."
            ),
        },
    },
]


def _ensure_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_write(path, payload):
    _ensure_dir()
    fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, str(path))
    except Exception:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def _read_json_file(path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _normalize_custom_preset(data):
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    return {
        "id": data.get("id") or uuid.uuid4().hex[:12],
        "name": name.strip(),
        "profile": _normalize_profile(data.get("profile")),
    }


def _normalize_custom_presets(data):
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        p = _normalize_custom_preset(item)
        if p:
            out.append(p)
    return out


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
        "stageHistory": [],
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
    # Старые чаты (до Day 15) могли сохранить этапы из прежнего набора
    # ("Сбор данных", "Сверка данных", "Готовое решение") — они не входят
    # в новый граф состояний, поэтому откатываемся в безопасный дефолт.
    if out["stage"] not in STAGES:
        out["stage"] = STAGE_WAITING
    return out


def _normalize_stage_history(data):
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        frm = item.get("from")
        to = item.get("to")
        if not isinstance(frm, str) or not isinstance(to, str):
            continue
        out.append({
            "from": frm,
            "to": to,
            "attempted": item.get("attempted") if isinstance(item.get("attempted"), str) else to,
            "ok": bool(item.get("ok", True)),
            "reason": item.get("reason") if isinstance(item.get("reason"), str) else "",
            "at": item.get("at") if isinstance(item.get("at"), (int, float)) else time.time(),
            "source": item.get("source") if isinstance(item.get("source"), str) else "auto",
        })
    return out[-200:]


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
    base["stageHistory"] = _normalize_stage_history(data.get("stageHistory"))
    base["totalPrompt"] = _normalize_int(data.get("totalPrompt"), 0)
    base["totalComp"] = _normalize_int(data.get("totalComp"), 0)
    return base


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
            return _root_with_single_chat(_new_chat("Чат 1"))
        active = data.get("activeChatId")
        if active not in chats:
            active = next(iter(chats))
        return {"chats": chats, "activeChatId": active}

    if isinstance(data, (dict, list)) and data:
        return _migrate_legacy(data)

    return _root_with_single_chat(_new_chat("Чат 1"))


# --- Чаты: каждая мутация держит CHATS_LOCK на весь цикл читай-меняй-пиши,
# иначе два быстрых подряд запроса (например, создание чата сразу после
# сохранения профиля) могут состязаться и потерять чужие изменения. ---

def _load_root_locked():
    raw = _read_json_file(STATE_FILE)
    root = _normalize_root(raw) if raw is not None else _root_with_single_chat(_new_chat("Чат 1"))
    if raw != root:
        _atomic_write(STATE_FILE, root)
    return root


def load_root():
    with CHATS_LOCK:
        return _load_root_locked()


def list_chats():
    with CHATS_LOCK:
        root = _load_root_locked()
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
    with CHATS_LOCK:
        root = _load_root_locked()
    return root["chats"].get(chat_id)


def create_chat(name=None):
    with CHATS_LOCK:
        root = _load_root_locked()
        n = len(root["chats"]) + 1
        chat = _new_chat(name or "Чат {}".format(n))
        root["chats"][chat["id"]] = chat
        root["activeChatId"] = chat["id"]
        root = _normalize_root(root)
        _atomic_write(STATE_FILE, root)
        return root["chats"][chat["id"]]


def update_chat(chat_id, patch):
    with CHATS_LOCK:
        root = _load_root_locked()
        chat = root["chats"].get(chat_id)
        if chat is None:
            return None
        for k, v in (patch or {}).items():
            if k == "id":
                continue
            chat[k] = v
        chat = _normalize_chat(chat)
        chat["id"] = chat_id
        chat["updatedAt"] = time.time()
        root["chats"][chat_id] = chat
        root = _normalize_root(root)
        _atomic_write(STATE_FILE, root)
        return root["chats"][chat_id]


def delete_chat(chat_id):
    with CHATS_LOCK:
        root = _load_root_locked()
        if chat_id in root["chats"]:
            del root["chats"][chat_id]
        if not root["chats"]:
            chat = _new_chat("Чат 1")
            root["chats"][chat["id"]] = chat
            root["activeChatId"] = chat["id"]
        elif root["activeChatId"] == chat_id:
            root["activeChatId"] = next(iter(root["chats"]))
        root = _normalize_root(root)
        _atomic_write(STATE_FILE, root)
        return root["activeChatId"]


def set_active_chat(chat_id):
    with CHATS_LOCK:
        root = _load_root_locked()
        if chat_id in root["chats"]:
            root["activeChatId"] = chat_id
            root = _normalize_root(root)
            _atomic_write(STATE_FILE, root)
        return root["activeChatId"]


def transition_chat_stage(chat_id, target_stage, source="manual"):
    """Явный контролируемый переход состояния задачи — тот же граф и та же
    функция task_states.transition(), что и в автоматической классификации
    внутри agent.py, только вызванный напрямую (например, по кнопке в UI или
    из теста), а не предложенный LLM. Недопустимый переход НЕ применяется —
    состояние остаётся прежним, а причина отказа возвращается вызывающему."""
    with CHATS_LOCK:
        root = _load_root_locked()
        chat = root["chats"].get(chat_id)
        if chat is None:
            return None

        current_stage = (chat.get("taskState") or {}).get("stage", STAGE_WAITING)
        if current_stage not in STAGES:
            current_stage = STAGE_WAITING
        ok, resulting_stage, message = fsm_transition(current_stage, target_stage)

        entry = {
            "from": current_stage, "to": resulting_stage, "attempted": target_stage,
            "ok": ok, "reason": message, "at": time.time(), "source": source,
        }
        history = list(chat.get("stageHistory") or [])
        history.append(entry)
        chat["stageHistory"] = history[-200:]

        if ok:
            chat["taskState"] = {
                "stage": resulting_stage,
                "step": "",
                "expectedAction": STAGE_DESCRIPTIONS.get(resulting_stage, ""),
            }

        chat = _normalize_chat(chat)
        chat["id"] = chat_id
        chat["updatedAt"] = time.time()
        root["chats"][chat_id] = chat
        root = _normalize_root(root)
        _atomic_write(STATE_FILE, root)

        return {"ok": ok, "message": message, "entry": entry, "chat": root["chats"][chat_id]}


# --- Пресеты профиля: свой файл, свой лок, общие для всех чатов. ---

def migrate_legacy_presets():
    """Одноразовая миграция при старте сервера: раньше кастомные пресеты жили
    внутри chat_history.json (customPresets). Переносим их в presets.json и
    убираем поле из chat_history.json — иначе первый же load_root() из любого
    запроса молча перезапишет файл уже без customPresets (нормализация чатов
    больше не знает про это поле) и данные потеряются до того, как мы успеем
    их прочитать."""
    with CHATS_LOCK, PRESETS_LOCK:
        raw_root = _read_json_file(STATE_FILE)
        if not isinstance(raw_root, dict) or "customPresets" not in raw_root:
            return
        legacy = raw_root.pop("customPresets")
        if not PRESETS_FILE.is_file():
            _atomic_write(PRESETS_FILE, {"customPresets": _normalize_custom_presets(legacy)})
        _atomic_write(STATE_FILE, raw_root)


def _load_custom_presets_locked():
    raw = _read_json_file(PRESETS_FILE)
    custom = _normalize_custom_presets(raw.get("customPresets") if isinstance(raw, dict) else None)
    if raw is None:
        _atomic_write(PRESETS_FILE, {"customPresets": custom})
    return custom


def list_profile_presets():
    with PRESETS_LOCK:
        custom = _load_custom_presets_locked()
    builtin = [dict(p, builtin=True) for p in PROFILE_PRESETS]
    return builtin + [dict(p, builtin=False) for p in custom]


def add_custom_preset(name, profile):
    preset = _normalize_custom_preset({"name": name, "profile": profile})
    if preset is None:
        return None
    with PRESETS_LOCK:
        custom = _load_custom_presets_locked()
        custom.append(preset)
        _atomic_write(PRESETS_FILE, {"customPresets": custom})
    return dict(preset, builtin=False)


def delete_custom_preset(preset_id):
    with PRESETS_LOCK:
        custom = _load_custom_presets_locked()
        before = len(custom)
        custom = [p for p in custom if p["id"] != preset_id]
        changed = len(custom) != before
        if changed:
            _atomic_write(PRESETS_FILE, {"customPresets": custom})
    return changed
