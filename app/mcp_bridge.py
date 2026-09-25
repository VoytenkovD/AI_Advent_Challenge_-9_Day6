# -*- coding: utf-8 -*-
"""Мост между агентом и MCP-сервером (mcp_server.py, GitHub API).

complete_with_tools() — замена llm.complete() для случая, когда в настройках
включена галочка MCP: запускает MCP-сервер по stdio, передаёт его инструменты
в LLM, выполняет запрошенные моделью вызовы через MCP и возвращает финальный ответ.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

from llm import LlmError, complete

SERVER_SCRIPT = Path(__file__).resolve().parent / "mcp_server.py"
MAX_TOOL_STEPS = 6
RESULT_PREVIEW_CHARS = 1500

MCP_SYSTEM_HINT = (
    "\n\n## Инструменты MCP\n"
    "Тебе доступны инструменты MCP-сервера GitHub (сведения о репозиториях, последние коммиты). "
    "Если вопрос касается GitHub-репозиториев — ОБЯЗАТЕЛЬНО вызови инструмент и опирайся на его "
    "результат, не выдумывай цифры. Если вопрос не про GitHub — отвечай как обычно."
)

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    MCP_IMPORT_ERROR = None
except ImportError as e:  # пакет mcp не установлен
    MCP_IMPORT_ERROR = str(e)


def _server_params():
    if MCP_IMPORT_ERROR:
        raise LlmError("Пакет mcp не установлен: pip install -r requirements.txt")
    return StdioServerParameters(command=sys.executable, args=[str(SERVER_SCRIPT)])


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
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = (await session.list_tools()).tools
            return {
                "server": init.serverInfo.name,
                "tools": [{
                    "name": t.name,
                    "description": t.description or "",
                    "params": list((t.inputSchema.get("properties") or {}).keys()),
                } for t in tools],
            }


def list_tools():
    return asyncio.run(_list_tools_async())


# ---------- ответ модели с вызовом инструментов ----------

async def _complete_with_tools_async(provider_id, model_id, messages, config):
    started = time.time()
    msgs = list(messages)
    calls_log = []
    usage = {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}

    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
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
    return asyncio.run(_complete_with_tools_async(provider_id, model_id, messages, config))
