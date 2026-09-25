# -*- coding: utf-8 -*-
"""Оркестратор MCP: агент ↔ несколько MCP-серверов (Day 17–20).

Серверы регистрируются в app/mcp_servers.json:
  • github  — GitHub + планировщик + пайплайн (постоянный процесс, streamable HTTP)
  • weather — погода Open-Meteo (stdio, запускается на время запроса)
  • notes   — заметки в файлах (stdio)

Оркестратор подключается ко всем включённым серверам, собирает их инструменты
в единый каталог с именами «<сервер>__<инструмент>», отдаёт каталог LLM и
маршрутизирует каждый вызов модели на нужный сервер. Все вызовы логируются
с указанием сервера и шага — по логу проверяется выбор и порядок инструментов.
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from llm import LlmError, complete

APP_DIR = Path(__file__).resolve().parent
REGISTRY_FILE = APP_DIR / "mcp_servers.json"
MCP_PORT = int(os.getenv("MCP_PORT", "5183"))
SEP = "__"  # разделитель «сервер__инструмент» в именах функций для LLM
MAX_TOOL_STEPS = 15
RESULT_PREVIEW_CHARS = 1500
EXPORTS_DIR = Path(os.getenv("SCHEDULER_DB") or APP_DIR / "data" / "scheduler.db").parent / "exports"

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamablehttp_client
    MCP_IMPORT_ERROR = None
except ImportError as e:  # пакет mcp не установлен
    MCP_IMPORT_ERROR = str(e)


# ═════════════════════════════ реестр серверов ═════════════════════════════

def load_registry():
    servers = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))["servers"]
    for s in servers:
        if s.get("url"):
            s["url"] = s["url"].replace("5183", str(MCP_PORT))  # переопределение порта для тестов
    return servers


def get_server(server_id):
    for s in load_registry():
        if s["id"] == server_id:
            return s
    raise LlmError("MCP-сервер «{}» не зарегистрирован".format(server_id))


def _selected(server_ids):
    reg = load_registry()
    if server_ids is None:
        return reg
    return [s for s in reg if s["id"] in server_ids]


MCP_URL = get_server("github")["url"]


def mcp_system_hint(server_ids=None):
    titles = ", ".join("{} ({})".format(s["id"], s["title"]) for s in _selected(server_ids))
    return (
        "\n\n## Инструменты MCP (несколько серверов)\n"
        "Подключены MCP-серверы: {}. Имя каждого инструмента имеет вид «<сервер>__<инструмент>».\n"
        "- github: данные репозиториев, поиск, пайплайн search → summarize → save_to_file (шаги передают "
        "search_id/summary_id), планировщик (наблюдение за репозиторием, напоминания, регулярные сводки).\n"
        "- weather: текущая погода и прогноз по городу.\n"
        "- notes: заметки пользователя — сохранить, дописать, прочитать, список.\n"
        "Правила: для фактических данных ОБЯЗАТЕЛЬНО вызывай инструменты нужного сервера, не выдумывай. "
        "Сложную задачу разбивай на шаги и выполняй их по порядку: сначала получи данные, потом обработай, "
        "потом сохрани. Если следующему шагу нужен результат предыдущего — дождись его и передай. "
        "Независимые запросы можно делать в одном шаге. В финальном ответе кратко перечисли, что было "
        "сделано и где сохранено.\n"
        "Текущие дата и время: {}.".format(titles, datetime.now().strftime("%d.%m.%Y %H:%M"))
    )


# ═════════════════════════════ процессы и подключения ═════════════════════════════

def _port_open(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def ensure_server(server_id="github", wait_sec=15):
    """Поднимает постоянный HTTP-сервер из реестра, если он ещё не слушает порт."""
    if MCP_IMPORT_ERROR:
        raise LlmError("Пакет mcp не установлен: pip install -r requirements.txt")
    s = get_server(server_id)
    if s["transport"] != "http":
        return False
    u = urlparse(s["url"])
    if _port_open(u.hostname, u.port):
        return False
    if not s.get("autostart"):
        raise LlmError("MCP-сервер «{}» недоступен: {}".format(server_id, s["url"]))
    subprocess.Popen([sys.executable, str(APP_DIR / s["script"])], cwd=str(APP_DIR))
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        if _port_open(u.hostname, u.port):
            return True
        time.sleep(0.3)
    raise LlmError("MCP-сервер «{}» не запустился за {} с".format(server_id, wait_sec))


async def _open(stack, s):
    """Открывает сессию с сервером s внутри AsyncExitStack. Возвращает (session, init)."""
    if s["transport"] == "http":
        ensure_server(s["id"])
        read, write, _ = await stack.enter_async_context(streamablehttp_client(s["url"]))
    else:
        params = StdioServerParameters(command=sys.executable, args=[str(APP_DIR / s["script"])],
                                       env=dict(os.environ))
        read, write = await stack.enter_async_context(stdio_client(params))
    session = await stack.enter_async_context(ClientSession(read, write))
    init = await session.initialize()
    return session, init


class Orchestrator:
    """Каталог инструментов нескольких серверов + маршрутизация вызовов."""

    def __init__(self):
        self.sessions = {}   # server_id -> ClientSession
        self.routes = {}     # "server__tool" -> (server_id, tool_name)
        self.catalog = []    # tools в формате Chat Completions
        self.status = []     # [{id, title, transport, ok, error, tools:[...]}]

    async def connect(self, stack, servers):
        for s in servers:
            entry = {"id": s["id"], "title": s["title"], "transport": s["transport"], "ok": False, "tools": []}
            try:
                session, init = await _open(stack, s)
                tools = (await session.list_tools()).tools
            except Exception as e:  # один упавший сервер не ломает остальные
                entry["error"] = _flatten_error(e)
                self.status.append(entry)
                continue
            self.sessions[s["id"]] = session
            entry.update(ok=True, server_name=init.serverInfo.name)
            for t in tools:
                full = "{}{}{}".format(s["id"], SEP, t.name)
                self.routes[full] = (s["id"], t.name)
                self.catalog.append({"type": "function", "function": {
                    "name": full,
                    "description": "[{}] {}".format(s["id"], t.description or ""),
                    "parameters": t.inputSchema,
                }})
                entry["tools"].append({"name": t.name, "description": t.description or "",
                                       "params": list((t.inputSchema.get("properties") or {}).keys())})
            self.status.append(entry)

    async def route(self, full_name, args):
        """Маршрутизация: имя из LLM → (сервер, инструмент) → вызов в нужной сессии."""
        if full_name not in self.routes:
            return None, full_name, True, "Неизвестный инструмент: {}. Доступны: {}".format(
                full_name, ", ".join(self.routes))
        server_id, tool = self.routes[full_name]
        result = await self.sessions[server_id].call_tool(tool, args)
        return server_id, tool, bool(result.isError), _result_text(result)


def _result_text(result):
    parts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
    return "\n".join(parts) or "(пустой результат)"


def _flatten_error(e):
    while isinstance(e, BaseExceptionGroup):
        e = e.exceptions[0]
    return str(e) or type(e).__name__


def _run(coro):
    """asyncio.run + разворачивание ExceptionGroup (anyio оборачивает в неё ошибки) в понятную LlmError."""
    try:
        return asyncio.run(coro)
    except BaseExceptionGroup as eg:
        e = eg
        while isinstance(e, BaseExceptionGroup):
            e = e.exceptions[0]
        if isinstance(e, LlmError):
            raise e from None
        raise LlmError(str(e)) from None


# ═════════════════════════════ публичные функции ═════════════════════════════

async def _servers_async(server_ids):
    async with AsyncExitStack() as stack:
        orch = Orchestrator()
        await orch.connect(stack, _selected(server_ids))
        return orch.status


def list_servers(server_ids=None):
    """Статус всех (или выбранных) серверов и их инструменты — для панели настроек."""
    return _run(_servers_async(server_ids))


def list_tools():
    """Совместимость с Day 17–19: инструменты сервера github."""
    st = list_servers(["github"])[0]
    if not st["ok"]:
        raise LlmError(st.get("error") or "github недоступен")
    return {"server": st.get("server_name"), "tools": st["tools"]}


async def _call_tool_async(server_id, name, args):
    async with AsyncExitStack() as stack:
        session, _ = await _open(stack, get_server(server_id))
        result = await session.call_tool(name, args or {})
    text = _result_text(result)
    if result.isError:
        raise LlmError(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}


def call_tool(name, args=None, server_id="github"):
    """Прямой вызов инструмента конкретного сервера (панели «Сводки», «Пайплайн»)."""
    return _run(_call_tool_async(server_id, name, args))


async def _complete_with_tools_async(provider_id, model_id, messages, config, server_ids):
    started = time.time()
    msgs = list(messages)
    calls_log = []
    usage = {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}

    async with AsyncExitStack() as stack:
        orch = Orchestrator()
        await orch.connect(stack, _selected(server_ids))
        if not orch.catalog:
            raise LlmError("Ни один MCP-сервер не доступен: " + "; ".join(
                "{}: {}".format(s["id"], s.get("error")) for s in orch.status))

        for step in range(1, MAX_TOOL_STEPS + 2):
            # на последнем шаге инструменты не передаём — модель обязана ответить текстом
            step_tools = orch.catalog if step <= MAX_TOOL_STEPS else None
            result = await asyncio.to_thread(complete, provider_id, model_id, msgs, config, step_tools)
            for k in usage:
                usage[k] += (result.get("usage") or {}).get(k, 0)

            tool_calls = result.get("tool_calls") or []
            if not tool_calls:
                result["usage"] = usage
                result["latency_ms"] = round((time.time() - started) * 1000)
                result["mcp_calls"] = calls_log
                result["mcp_servers"] = [{"id": s["id"], "ok": s["ok"], "error": s.get("error")}
                                         for s in orch.status]
                return result

            msgs.append({"role": "assistant", "content": result.get("text") or "", "tool_calls": tool_calls})
            for call in tool_calls:
                full = call["function"]["name"]
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                t0 = time.time()
                try:
                    server_id, tool, is_error, text = await orch.route(full, args)
                except Exception as e:
                    server_id, tool = orch.routes.get(full, (None, full))
                    is_error, text = True, "Ошибка вызова: {}".format(_flatten_error(e))
                calls_log.append({
                    "n": len(calls_log) + 1,
                    "step": step,
                    "server": server_id,
                    "tool": tool,
                    "name": full,
                    "args": args,
                    "isError": is_error,
                    "result": text[:RESULT_PREVIEW_CHARS],
                    "ms": round((time.time() - t0) * 1000),
                })
                msgs.append({"role": "tool", "tool_call_id": call["id"], "content": text})

    raise LlmError("MCP: модель не дала финальный ответ")


def complete_with_tools(provider_id, model_id, messages, config, server_ids=None):
    if server_ids is None:
        server_ids = config.get("mcpServers")  # None = все зарегистрированные
    return _run(_complete_with_tools_async(provider_id, model_id, messages, config, server_ids))
