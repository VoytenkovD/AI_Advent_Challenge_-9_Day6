# -*- coding: utf-8 -*-
import json
import os
import pathlib
import time
import urllib.error
import urllib.request

TIMEOUT_SEC = 300

PROVIDERS = {
    "ai-public": {
        "base_url": "https://ai-public.a101.ru/api",
        "key_file": pathlib.Path.home() / ".secrets" / "ai-public",
        "env_var": "AI_PUBLIC_API_KEY",
        "key_cache": None,
    },
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "key_file": pathlib.Path.home() / ".secrets" / "nvidia",
        "env_var": "NVIDIA_API_KEY",
        "key_cache": None,
    }
}

class LlmError(RuntimeError):
    pass

def get_api_key(provider_id):
    provider = PROVIDERS.get(provider_id)
    if not provider:
        raise LlmError(f"Неизвестный провайдер: {provider_id}")
    
    if provider["key_cache"]:
        return provider["key_cache"]
    
    if provider["key_file"].is_file():
        key = provider["key_file"].read_text(encoding="utf-8-sig").strip()
        if key:
            provider["key_cache"] = key
            return key
            
    key = (os.getenv(provider["env_var"]) or "").strip()
    if key:
        provider["key_cache"] = key
        return key
        
    raise LlmError(f"API-ключ для {provider_id} не найден.")

def get_models(provider_id):
    provider = PROVIDERS[provider_id]
    request = urllib.request.Request(
        f"{provider['base_url']}/models",
        headers={"Authorization": f"Bearer {get_api_key(provider_id)}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
            return [m["id"] for m in data.get("data", [])]
    except Exception as e:
        print(f"Ошибка получения моделей {provider_id}: {e}")
        return []

def complete(provider_id, model_id, messages, config, tools=None):
    """Вызов модели с расширенными настройками. tools — описания функций (tool calling)."""
    provider = PROVIDERS.get(provider_id)
    if not provider:
        raise LlmError(f"Неизвестный провайдер: {provider_id}")

    req_body = {
        "model": model_id,
        "messages": messages,
        "stream": False,
        "temperature": config.get("temperature", 0.7),
        "top_p": config.get("topP", 1.0),
        "frequency_penalty": config.get("frequencyPenalty", 0.0),
        "presence_penalty": config.get("presencePenalty", 0.0),
        "max_tokens": config.get("maxTokens", 4000),
    }

    if config.get("responseFormat") == "json_object":
        req_body["response_format"] = {"type": "json_object"}

    if tools:
        req_body["tools"] = tools
        req_body["tool_choice"] = "auto"

    body = json.dumps(req_body, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(
        f"{provider['base_url']}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {get_api_key(provider_id)}",
            "Content-Type": "application/json",
        },
    )

    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise LlmError(f"API {provider_id} вернул {e.code}: {detail}") from e
    except Exception as e:
        raise LlmError(f"Ошибка сети: {e}") from e

    try:
        choice = data["choices"][0]
    except (KeyError, IndexError) as e:
        raise LlmError("Неожиданный формат ответа API") from e

    usage = data.get("usage") or {}
    
    return {
        "text": choice.get("message", {}).get("content") or "",
        "tool_calls": choice.get("message", {}).get("tool_calls") or [],
        "finish_reason": choice.get("finish_reason"),
        "usage": {
            "total_tokens": usage.get("total_tokens", 0),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0)
        },
        "latency_ms": round((time.time() - started) * 1000),
    }
