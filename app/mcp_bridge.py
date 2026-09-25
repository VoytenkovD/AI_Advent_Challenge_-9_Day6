# -*- coding: utf-8 -*-
"""Мост между агентом и MCP-сервером (mcp_server.py: GitHub API + планировщик).

MCP-сервер — постоянный процесс (streamable HTTP), в нём крутится планировщик 24/7.
ensure_server() поднимает его при старте веб-сервера, если он ещё не запущен.

complete_with_tools() — замена llm.complete() для случая, когда в настройках
включена галочка MCP: передаёт инструменты сервера в LLM, выполняет запрошенные
моделью вызовы через MCP и возвращает финальный ответ.
call_tool() — прямой вызов инструмента (для панели «Сводки» в UI).
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from llm import LlmError, complete

SERVER_SCRIPT = Path(__file__).resolve().parent / "mcp_server.py"
MCP_HOST = "127.0.0.1"
MCP_PORT = int(os.getenv("MCP_PORT", "5183"))
MCP_URL = f"http://{MCP_HOST}:{MCP_PORT}/mcp"
MAX_TOOL_STEPS = 8
EXPORTS_DIR = Path(os.getenv("SCHEDULER_DB") or SERVER_SCRIPT.parent / "data" / "scheduler.db").parent / "exports"
RESULT_PREVIEW_CHARS = 1500


def mcp_system_hint():
    return (
        "\n\n## Инструменты MCP\n"
        "Тебе доступны инструменты MCP-сервера: данные GitHub-репозиториев и планировщик фоновых задач "
        "(периодический сбор данных о репозитории, отложенные напоминания, регулярная сводка, список и "
        "отмена задач, агрегированная сводка за период). Планировщик работает 24/7 и публикует сводки и "
        "напоминания в ленту «Сводки».\n"
        "Есть пайплайн из трёх инструментов: search_github_repos (найти) → summarize_search (обработать) → "
        "save_to_file (сохранить); шаги передают друг другу ID артефактов. Если пользователь просит найти, "
        "обобщить и сохранить — вызови run_pipeline (вся цепочка одним вызовом) либо шаги по очереди, "
        "передавая search_id и summary_id из предыдущего шага. В ответе назови сохранённый файл.\n"
        "Если вопрос касается GitHub, расписаний, напоминаний или сводок — ОБЯЗАТЕЛЬНО вызови подходящий "
        "инструмент и опирайся на его результат, не выдумывай данные. Иначе отвечай как обычно.\n"
        "Текущие дата и время: {}.".format(datetime.now().strftime("%d.%m.%Y %H:%M"))
    )


try:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    MCP_IMPORT_ERROR = None
except ImportError as e:  # пакет mcp не установлен
    MCP_IMPORT_ERROR = str(e)


# ---------- процесс MCP-сервера ----------

def _port_open():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((MCP_HOST, MCP_PORT)) == 0


def ensure_server(wait_sec=15):
    """Запускает mcp_server.py фоновым процессом, если порт ещё не слушается."""
    if MCP_IMPORT_ERROR:
        raise LlmError("Пакет mcp не установлен: pip install -r requirements.txt")
    if _port_open():
        return False
    subprocess.Popen([sys.executable, str(SERVER_SCRIPT)], cwd=str(SERVER_SCRIPT.parent))
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        if _port_open():
            return True
        time.sleep(0.3)
    raise LlmError("MCP-сервер не запустился за {} с".format(wait_sec))


@asynccontextmanager
async def _session():
    ensure_server()
    async with streamablehttp_client(MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            yield session, init


def _to_openai_tools(mcp_tools):
    return [{
        "type": "function",
        "function": {"name": t.name, "description": t.description or "", "parameters": t.inputSchema},
    } for t in mcp_tools]


def _result_text(result):
    parts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
    return "\n".join(parts) or "(пустой результат)"


# ---------- список инструментов (для панели настроек) ----------

async def _list_tools_async():
    async with _session() as (session, init):
        tools = (await session.list_tools()).tools
        return {
            "server": init.serverInfo.name,
            "tools": [{
                "name": t.name,
                "description": t.description or "",
                "params": list((t.inputSchema.get("properties") or {}).keys()),
            } for t in tools],
        }


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
        if isinstance(e, OSError) or "connect" in type(e).__name__.lower():
            raise LlmError("MCP-сервер недоступен по адресу {}: {}".format(MCP_URL, e)) from None
        raise LlmError(str(e)) from None


def list_tools():
    return _run(_list_tools_async())


# ---------- прямой вызов инструмента ----------

async def _call_tool_async(name, args):
    async with _session() as (session, _):
        result = await session.call_tool(name, args or {})
        text = _result_text(result)
        if result.isError:
            raise LlmError(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}


def call_tool(name, args=None):
    return _run(_call_tool_async(name, args))


# ---------- ответ модели с вызовом инструментов ----------

async def _complete_with_tools_async(provider_id, model_id, messages, config):
    started = time.time()
    msgs = list(messages)
    calls_log = []
    usage = {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}

    async with _session() as (session, _):
        tools = _to_openai_tools((await session.list_tools()).tools)

        for step in range(MAX_TOOL_STEPS + 1):
            # на последнем шаге инструменты не передаём — модель обязана ответить текстом
            step_tools = tools if step < MAX_TOOL_STEPS else None
            result = await asyncio.to_thread(complete, provider_id, model_id, msgs, config, step_tools)
            for k in usage:
                usage[k] += (result.get("usage") or {}).get(k, 0)

            tool_calls = result.get("tool_calls") or []
            if not tool_calls:
                result["usage"] = usage
                result["latency_ms"] = round((time.time() - started) * 1000)
                result["mcp_calls"] = calls_log
                return result

            msgs.append({"role": "assistant", "content": result.get("text") or "", "tool_calls": tool_calls})
            for call in tool_calls:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                t0 = time.time()
                tool_result = await session.call_tool(name, args)
                text = _result_text(tool_result)
                calls_log.append({
                    "tool": name,
                    "args": args,
                    "isError": bool(tool_result.isError),
                    "result": text[:RESULT_PREVIEW_CHARS],
                    "ms": round((time.time() - t0) * 1000),
                })
                msgs.append({"role": "tool", "tool_call_id": call["id"], "content": text})

    raise LlmError("MCP: модель не дала финальный ответ")


def complete_with_tools(provider_id, model_id, messages, config):
    return _run(_complete_with_tools_async(provider_id, model_id, messages, config))
