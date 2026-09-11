# -*- coding: utf-8 -*-
"""Логика агента: сборка контекста, компрессия истории, вызов модели."""
from llm import LlmError, complete

PRESETS = {
    "assistant": "Ты полезный ИИ-ассистент. Отвечай кратко и по делу.",
    "stepwise": (
        "Решай задачу строго пошагово. "
        "Сначала перечисли, что дано и что требуется найти. "
        "Затем разбей решение на пронумерованные шаги."
    ),
    "analyst": (
        "Ты -- аналитик. Начни с разбора условия: выпиши все данные, явные и неявные допущения. "
        "Только после разбора дай своё решение."
    ),
    "engineer": (
        "Ты -- инженер. Тебя интересует работающая процедура. "
        "Дай конкретный алгоритм или расчёт и доведи его до результата."
    ),
    "critic": (
        "Ты -- критик и скептик. Сначала перечисли ловушки этой задачи и типичные ошибки. "
        "Затем реши задачу сам, обходя перечисленные ловушки."
    )
}

FORMAT_SYSTEM = (
    "Отвечай на русском языке в простом Markdown. "
    "Формулы и вычисления записывай обычным текстом в одну строку. "
    "Категорически не используй LaTeX: ни \\( \\), ни \\[ \\], ни \\frac, ни \\text."
)

SUMMARIZE_SYSTEM = (
    "Ты сжимаешь историю диалога в краткое резюме. "
    "Сохрани все факты, имена, числа, даты, решения и договорённости, "
    "которые важны для продолжения разговора. "
    "Отбрось вежливость, повторы и воду. "
    "Пиши сжато, от третьего лица, связным текстом или короткими пунктами. "
    "Не добавляй ничего от себя и не решай новые задачи."
)

SUMMARY_MAX_TOKENS = 700


class PolicyError(LlmError):
    pass


def _estimate_chars(messages):
    """Суммарная длина всех сообщений в символах."""
    return sum(len(m.get("content", "")) for m in messages)


def _build_system(config):
    """Собирает system-инструкцию агента."""
    preset_key = config.get("systemPromptPreset", "assistant")
    prompt = PRESETS.get(preset_key, PRESETS["assistant"]) + "\n\n" + FORMAT_SYSTEM

    max_words = config.get("maxWords", 0)
    if max_words > 0:
        prompt += f"\n\nУложись в {max_words} слов."

    if config.get("responseFormat") == "json_object":
        prompt += "\n\nВерни ответ строго в формате JSON."

    return prompt


def _do_summarize(provider_id, model_id, prev_summary, chunk):
    """Сворачивает кусок истории (и прошлое резюме) в новое резюме. Возвращает (текст, usage)."""
    parts = []
    if prev_summary:
        parts.append("Прежнее резюме:\n" + prev_summary)
    dialogue = "\n".join(
        "{}: {}".format(
            "Пользователь" if m.get("role") == "user" else "Ассистент",
            m.get("content", ""),
        )
        for m in chunk
    )
    parts.append("Новые сообщения:\n" + dialogue)
    parts.append("Обнови резюме с учётом новых сообщений. Верни только текст резюме.")

    result = complete(
        provider_id,
        model_id,
        [
            {"role": "system", "content": SUMMARIZE_SYSTEM},
            {"role": "user", "content": "\n\n".join(parts)},
        ],
        {"temperature": 0.0, "maxTokens": SUMMARY_MAX_TOKENS},
    )
    return result["text"], result["usage"]


def run_agent(question, agent_data):
    config = agent_data.get("config", {})
    history = agent_data.get("history", [])
    summary = agent_data.get("summary") or ""
    summary_up_to = int(agent_data.get("summaryUpTo") or 0)
    provider_id = config.get("provider", "ai-public")
    model_id = config.get("model")

    if not model_id:
        raise PolicyError("Модель не выбрана")

    # --- Входная политика ---
    max_input_chars = config.get("maxInputChars", 2000)
    if len(question) > max_input_chars:
        raise PolicyError(
            "Запрос превышает лимит в {} символов.".format(max_input_chars)
        )
    if not question.strip():
        raise PolicyError("Запрос не может быть пустым.")

    # --- Настройки компрессии ---
    mode = config.get("contextMode", "compressed")
    keep_recent = int(config.get("keepRecent", 6) or 6)
    summarize_every = int(config.get("summarizeEvery", 10) or 10)

    sys_prompt = _build_system(config)

    summary_usage = None
    summarized = False

    # --- Компрессия: обновляем резюме, если накопилось достаточно ---
    if mode == "compressed" and keep_recent >= 0 and summarize_every > 0:
        older_end = max(0, len(history) - keep_recent)
        # Если окно recent увеличили, clamped summary_up_to
        summary_up_to = min(summary_up_to, older_end)
        pending = max(0, older_end - summary_up_to)

        if pending >= summarize_every:
            chunk = history[summary_up_to:older_end]
            try:
                summary, summary_usage = _do_summarize(
                    provider_id, model_id, summary, chunk
                )
                summary_up_to = older_end
                summarized = True
            except LlmError:
                # Не даём сбою резюмирования убить ответ — просто сохраняем старое резюме
                pass

    # --- Сборка сообщений ---
    messages = [{"role": "system", "content": sys_prompt}]

    if mode == "compressed":
        if summary:
            messages.append(
                {
                    "role": "system",
                    "content": "Резюме предыдущей части диалога:\n" + summary,
                }
            )
        recent = history[-keep_recent:] if keep_recent > 0 else []
        for m in recent:
            messages.append(
                {"role": m.get("role", "user"), "content": m.get("content", "")}
            )
    else:
        for m in history:
            messages.append(
                {"role": m.get("role", "user"), "content": m.get("content", "")}
            )

    messages.append({"role": "user", "content": question})

    # --- Вызов модели ---
    result = complete(provider_id, model_id, messages, config)

    # --- Сравнение расхода токенов ---
    actual_chars = _estimate_chars(messages)
    sent_tokens = result["usage"].get("prompt_tokens", 0)
    # Калибруем оценку по реальному соотношению токены/символы
    ratio = (sent_tokens / actual_chars) if actual_chars > 0 else 0.27

    # Сколько бы стоила полная история (без сжатия)
    full_messages = [{"role": "system", "content": sys_prompt}]
    for m in history:
        full_messages.append(
            {"role": m.get("role", "user"), "content": m.get("content", "")}
        )
    full_messages.append({"role": "user", "content": question})
    full_chars = _estimate_chars(full_messages)
    full_estimate = round(ratio * full_chars)

    saved = full_estimate - sent_tokens
    saved_pct = round(saved / full_estimate * 100, 1) if full_estimate > 0 else 0.0

    st = (summary_usage or {}).get("total_tokens", 0)

    return {
        "text": result["text"],
        "finish_reason": result["finish_reason"],
        "usage": result["usage"],
        "latency_ms": result["latency_ms"],
        "summary": summary,
        "summaryUpTo": summary_up_to,
        "summarized": summarized,
        "comparison": {
            "mode": mode,
            "sentTokens": sent_tokens,
            "fullEstimateTokens": full_estimate,
            "savedTokens": saved,
            "savedPercent": saved_pct,
            "messagesSent": len(messages) - 1 - (1 if summary and mode == "compressed" else 0),
            "messagesTotal": len(history) + 1,
            "summaryTokensThisTurn": st,
        },
    }