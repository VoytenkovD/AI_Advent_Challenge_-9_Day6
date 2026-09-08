# -*- coding: utf-8 -*-
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

class PolicyError(LlmError):
    pass

def run_agent(question, agent_data):
    config = agent_data.get("config", {})
    history = agent_data.get("history", [])
    provider_id = config.get("provider", "ai-public")
    model_id = config.get("model")

    if not model_id:
        raise PolicyError("Модель не выбрана")

    # 1. Входная политика (Input Policy)
    max_input_chars = config.get("maxInputChars", 2000)
    if len(question) > max_input_chars:
        raise PolicyError(f"Запрос превышает лимит в {max_input_chars} символов.")
    if not question.strip():
        raise PolicyError("Запрос не может быть пустым.")

    # 2. Сборка контекста
    preset_key = config.get("systemPromptPreset", "assistant")
    sys_prompt = PRESETS.get(preset_key, PRESETS["assistant"]) + "\n\n" + FORMAT_SYSTEM

    max_words = config.get("maxWords", 0)
    if max_words > 0:
        sys_prompt += f"\n\nУложись в {max_words} слов."
        
    if config.get("responseFormat") == "json_object":
        sys_prompt += "\n\nВерни ответ строго в формате JSON."

    messages = [{"role": "system", "content": sys_prompt}]

    history_depth = config.get("historyDepth", 5)
    if history_depth > 0 and history:
        tail = history[-history_depth:]
        for msg in tail:
            messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})

    messages.append({"role": "user", "content": question})

    # 3. Вызов модели
    return complete(provider_id, model_id, messages, config)
