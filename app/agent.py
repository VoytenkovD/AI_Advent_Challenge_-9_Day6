# -*- coding: utf-8 -*-
"""Логика агента: три стратегии управления контекстом."""
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
    ),
}

FORMAT_SYSTEM = (
    "Отвечай на русском языке в простом Markdown. "
    "Формулы и вычисления записывай обычным текстом в одну строку. "
    "Категорически не используй LaTeX."
)

FACTS_EXTRACT_SYSTEM = (
    "Ты извлекаешь важные факты из сообщения пользователя в формате ключ=значение. "
    "Сохрани: имена, должности, цели, ограничения, бюджет, сроки, предпочтения, стек технологий, "
    "договорённости и любые другие значимые данные. "
    "Верни строго JSON-список: [{\"key\": \"...\", \"value\": \"...\"}, ...]. "
    "Если в сообщении нет новых фактов, верни пустой список []. "
    "Не добавляй ничего от себя и не пиши комментарии."
)

SUMMARY_MAX_TOKENS = 700

MEMORY_SUGGEST_PROMPT = (
    "Проанализируй последний ответ ассистента и диалог. Предложи, какие данные стоит сохранить "
    "в рабочую память (для текущей задачи) и в долговременную память (на будущее).\n\n"
    "Рабочая память — данные текущей задачи: цели, ограничения, промежуточные результаты, "
    "спецификации, сроки, бюджет, текущий статус.\n"
    "Долговременная память — знания на будущее: имя и предпочтения пользователя, "
    "принятые решения, изученные уроки, полезные факты.\n\n"
    "Верни СТРОГО JSON: "
    '{"working":[{"key":"...","value":"..."}],"long_term":[{"key":"...","value":"..."}]}\n'
    "Если сохранять нечего — верни пустые списки. Не добавляй комментарии."
)


STAGES = ["Ожидание задачи", "Сбор данных", "Сверка данных", "Готовое решение"]
DEFAULT_TASK_STATE = {"stage": "Ожидание задачи", "step": "", "expectedAction": ""}

TASK_STATE_PROMPT = (
    "Ты определяешь этап выполнения задачи в диалоге пользователя с ассистентом. "
    "Используется конечный автомат с четырьмя этапами (используй эти строки ТОЧНО, без изменений):\n"
    "1. \"Ожидание задачи\" — пользователь ещё не сформулировал задачу, либо предыдущая задача уже "
    "завершена и ассистент ждёт новую.\n"
    "2. \"Сбор данных\" — задача сформулирована, но не хватает данных; ассистент уточняет недостающие "
    "детали.\n"
    "3. \"Сверка данных\" — все нужные данные собраны, ассистент показывает их пользователю на "
    "проверку перед выполнением.\n"
    "4. \"Готовое решение\" — ассистент только что выдал итоговый результат по задаче.\n\n"
    "Определи по новому сообщению пользователя и новому ответу ассистента:\n"
    "- stage: строго одна из четырёх строк выше.\n"
    "- step: краткое описание (3-8 слов) текущего шага, например «уточняем бюджет и сроки поездки».\n"
    "- expectedAction: какого именно действия ассистент ждёт от пользователя дальше (1 короткая фраза), "
    "пустая строка, если ничего не ждёт.\n\n"
    "Верни строго JSON: {\"stage\":\"...\",\"step\":\"...\",\"expectedAction\":\"...\"}. Без комментариев."
)

TOPIC_EXTRACT_PROMPT = (
    "Сформулируй главную тему диалога одной короткой фразой (3-6 слов) по первому сообщению "
    "пользователя и ответу ассистента. Без кавычек и точки в конце. Верни только фразу, без комментариев."
)


def _build_task_state_context(task_state):
    """Формирует блок состояния задачи (конечный автомат) для system prompt."""
    ts = task_state or {}
    stage = ts.get("stage") or "Ожидание задачи"
    step = (ts.get("step") or "").strip()
    expected = (ts.get("expectedAction") or "").strip()

    parts = ["\n## Состояние задачи (конечный автомат)\n"]
    parts.append("Текущий этап: {}".format(stage))
    if step:
        parts.append("Текущий шаг: {}".format(step))
    if expected:
        parts.append("Ожидаемое действие пользователя: {}".format(expected))
    parts.append(
        "\nЭтапы идут в порядке: «Ожидание задачи» → «Сбор данных» → «Сверка данных» → "
        "«Готовое решение» → снова «Ожидание задачи». Правила:\n"
        "- На этапе «Сбор данных» НЕ переспрашивай то, что уже есть в истории диалога — уточняй "
        "только то, чего действительно не хватает.\n"
        "- На этапе «Сверка данных» кратко покажи собранные данные и попроси подтверждения, не "
        "задавай новых вопросов без необходимости.\n"
        "- На этапе «Готовое решение» выдай итоговый результат по задаче.\n"
        "- Если пользователь возвращается к разговору после паузы, продолжай ИМЕННО с текущего "
        "этапа и шага — не начинай объяснения и уточнения заново."
    )
    return "\n".join(parts)


def _classify_task_state(provider_id, model_id, question, answer_text, current_state):
    """Вызов LLM-классификатора для определения этапа/шага/ожидаемого действия."""
    import json as _json

    cur = current_state or dict(DEFAULT_TASK_STATE)
    prompt = (
        "Предыдущее состояние задачи:\n"
        "stage = {}\nstep = {}\nexpectedAction = {}\n\n"
        "Новое сообщение пользователя:\n{}\n\n"
        "Новый ответ ассистента:\n{}"
    ).format(
        cur.get("stage", "Ожидание задачи"), cur.get("step", ""), cur.get("expectedAction", ""),
        question, answer_text[:1000],
    )

    try:
        result = complete(
            provider_id, model_id,
            [{"role": "system", "content": TASK_STATE_PROMPT},
             {"role": "user", "content": prompt}],
            {"temperature": 0.0, "maxTokens": 200},
        )
        text = result["text"].strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if len(lines) > 2 else lines)
        data = _json.loads(text)
        stage = data.get("stage") if data.get("stage") in STAGES else "Ожидание задачи"
        return {
            "stage": stage,
            "step": str(data.get("step") or ""),
            "expectedAction": str(data.get("expectedAction") or ""),
        }, result
    except Exception:
        return dict(cur), None


def _extract_topic(provider_id, model_id, question, answer_text):
    """Короткая фраза-тема чата, извлекается один раз после первого обмена репликами."""
    prompt = "Сообщение пользователя: {}\nОтвет ассистента: {}".format(question, answer_text[:500])
    try:
        result = complete(
            provider_id, model_id,
            [{"role": "system", "content": TOPIC_EXTRACT_PROMPT},
             {"role": "user", "content": prompt}],
            {"temperature": 0.0, "maxTokens": 30},
        )
        topic = result["text"].strip().strip('"').strip("'").strip(".")
        return topic, result
    except Exception:
        return "", None


def _build_profile_context(profile):
    """Формирует блок персонализации из профиля пользователя."""
    profile = profile or {}
    identity = (profile.get("identity") or "").strip()
    style = (profile.get("style") or "").strip()
    fmt = (profile.get("format") or "").strip()
    constraints = (profile.get("constraints") or "").strip()

    if not (identity or style or fmt or constraints):
        return ""

    parts = ["\n## Профиль пользователя (персонализация)\n"]
    if identity:
        parts.append("Личность и обстоятельства: {}".format(identity))
    if style:
        parts.append("Требуемый стиль ответа: {}".format(style))
    if fmt:
        parts.append("Требуемый формат ответа: {}".format(fmt))
    if constraints:
        parts.append("Ограничения (чего нет/что нельзя предполагать): {}".format(constraints))
    parts.append(
        "\nОбязательно учитывай эти данные в каждом ответе: подбирай терминологию и тон под "
        "уровень пользователя из «Личности», используй заданный «Стиль» и «Формат», и никогда "
        "не предполагай и не советуй то, что противоречит «Ограничениям». Если для ответа нужны "
        "детали, которых нет в профиле, — уточни их у пользователя, опираясь на «Личность»."
    )
    return "\n".join(parts)


def _build_memory_context(memory):
    """Формирует блок памяти для system prompt."""
    parts = ["\n## Память агента\n"]
    wm = (memory or {}).get("working") or []
    lm = (memory or {}).get("long_term") or []

    if wm:
        parts.append("### Рабочая память (текущая задача):")
        for e in wm:
            parts.append("- {} = {}".format(e.get("key", "?"), e.get("value", "?")))
    if lm:
        parts.append("### Долговременная память (профиль и знания):")
        for e in lm:
            parts.append("- {} = {}".format(e.get("key", "?"), e.get("value", "?")))
    if wm:
        parts.append(
            "\nВАЖНО: если вопрос пользователя не относится к данным в рабочей памяти "
            "(отвлечённая тема, общий вопрос, шутка), отвечай на него напрямую, "
            "игнорируя рабочую память."
        )
    return "\n".join(parts) if len(parts) > 1 else ""


def _suggest_memory(provider_id, model_id, question, answer_text, memory):
    """Вызов LLM для предложений по памяти."""
    import json as _json

    wm_parts = []
    for e in (memory.get("working") or []):
        wm_parts.append("{} = {}".format(e.get("key", ""), e.get("value", "")))
    lm_parts = []
    for e in (memory.get("long_term") or []):
        lm_parts.append("{} = {}".format(e.get("key", ""), e.get("value", "")))

    prompt = (
        "Вопрос пользователя: {}\n"
        "Ответ ассистента: {}\n\n"
        "Текущая рабочая память:\n{}\n\n"
        "Текущая долговременная память:\n{}"
    ).format(
        question,
        answer_text[:800],
        "\n".join(wm_parts) if wm_parts else "(пусто)",
        "\n".join(lm_parts) if lm_parts else "(пусто)",
    )

    try:
        result = complete(
            provider_id, model_id,
            [{"role": "system", "content": MEMORY_SUGGEST_PROMPT},
             {"role": "user", "content": prompt}],
            {"temperature": 0.0, "maxTokens": 500},
        )
        text = result["text"].strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if len(lines) > 2 else lines)
        return _json.loads(text), result
    except Exception:
        return None, None


class PolicyError(LlmError):
    pass


def _build_system(config, memory=None, profile=None, task_state=None):
    preset_key = config.get("systemPromptPreset", "assistant")
    prompt = PRESETS.get(preset_key, PRESETS["assistant"]) + "\n\n" + FORMAT_SYSTEM
    max_words = config.get("maxWords", 0)
    if max_words > 0:
        prompt += "\n\nУложись в {} слов.".format(max_words)
    if config.get("responseFormat") == "json_object":
        prompt += "\n\nВерни ответ строго в формате JSON."
    profile_block = _build_profile_context(profile)
    if profile_block:
        prompt += "\n" + profile_block
    prompt += "\n" + _build_task_state_context(task_state)
    if memory:
        memory_block = _build_memory_context(memory)
        if memory_block:
            prompt += "\n" + memory_block
    return prompt


def _extract_facts(provider_id, model_id, question, facts_now):
    """Вызов модели для извлечения фактов из сообщения пользователя."""
    existing = json_encode_facts_for_prompt(facts_now)
    prompt = (
        "Текущие известные факты:\n{}\n\n"
        "Новое сообщение пользователя:\n{}\n\n"
        "Извлеки ВСЕ факты (старые + новые) в JSON-список [{{\"key\":..., \"value\":...}}]. "
        "Если факт изменился, обнови его значение."
    ).format(existing, question)

    try:
        result = complete(
            provider_id, model_id,
            [
                {"role": "system", "content": FACTS_EXTRACT_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            {"temperature": 0.0, "maxTokens": 600},
        )
        return _parse_facts_json(result["text"]), result
    except Exception:
        return facts_now, None


def _parse_facts_json(text):
    import json as _json

    text = text.strip()
    # модель может обернуть в ```json ... ```
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
        text = text.strip()
    try:
        items = _json.loads(text)
        if isinstance(items, list):
            return items
    except _json.JSONDecodeError:
        pass
    return []


def json_encode_facts_for_prompt(facts):
    if not facts:
        return "(нет)"
    return "\n".join("- {} = {}".format(f.get("key", "?"), f.get("value", "?")) for f in facts)


def _build_facts_prompt(facts):
    if not facts:
        return ""
    lines = ["\nКлючевые факты из диалога:"]
    for f in facts:
        lines.append("- {} = {}".format(f.get("key", "?"), f.get("value", "?")))
    return "\n".join(lines)


def _resolve_history(agent_data):
    """Возвращает эффективную историю с учётом стратегии и ветки."""
    history = agent_data.get("history") or []
    branches = agent_data.get("branches") or {}
    active_branch = agent_data.get("activeBranch")

    if active_branch and active_branch in branches:
        return branches[active_branch]
    return history


def run_agent(question, agent_data):
    config = agent_data.get("config", {})
    provider_id = config.get("provider", "ai-public")
    model_id = config.get("model")
    strategy = config.get("contextMode", "sliding")

    if not model_id:
        raise PolicyError("Модель не выбрана")

    # --- Входная политика ---
    max_input_chars = config.get("maxInputChars")
    if max_input_chars is None:
        max_input_chars = 2000
    max_input_chars = int(max_input_chars)

    if len(question) > max_input_chars:
        raise PolicyError("Запрос превышает лимит в {} символов.".format(max_input_chars))
    if not question.strip():
        raise PolicyError("Запрос не может быть пустым.")

    sys_prompt = _build_system(
        config, agent_data.get("memory"), agent_data.get("profile"), agent_data.get("taskState")
    )

    # --- Получаем историю с учётом ветки ---
    history = _resolve_history(agent_data)
    
    keep = config.get("keepRecent")
    if keep is None:
        keep = 6
    keep = int(keep)

    # --- Новые факты/состояние ---
    facts = agent_data.get("facts") or []

    result_extra = {}
    summary_usage = None

    # ============================
    # СТРАТЕГИЯ 1: Sliding Window
    # ============================
    if strategy == "sliding":
        messages = [{"role": "system", "content": sys_prompt}]
        tail = history[-keep:] if keep > 0 else history
        for m in tail:
            messages.append({"role": m.get("role", "user"), "content": m.get("content", "")})
        messages.append({"role": "user", "content": question})
        result = complete(provider_id, model_id, messages, config)

    # ============================
    # СТРАТЕГИЯ 2: Sticky Facts
    # ============================
    elif strategy == "facts":
        # Обновляем факты после каждого сообщения пользователя
        every = int(config.get("factsUpdateEvery", 1) or 1)
        facts_up_to = int(agent_data.get("factsUpTo") or 0)
        new_count = len(history) - facts_up_to
        if new_count >= every and every > 0:
            # собираем текст всех новых сообщений пользователя
            new_user_texts = []
            for m in history[facts_up_to:]:
                if m.get("role") == "user":
                    new_user_texts.append(m.get("content", ""))
            combined = " | ".join(new_user_texts[-3:])
            facts_usage = None
            try:
                facts, fres = _extract_facts(provider_id, model_id, combined, facts)
                summary_usage = fres.get("usage") if fres else None
            except Exception:
                pass
            facts_up_to = len(history)
            result_extra["facts"] = facts
            result_extra["factsUpTo"] = facts_up_to

        messages = [{"role": "system", "content": sys_prompt}]
        fp = _build_facts_prompt(facts)
        if fp:
            messages.append({"role": "system", "content": fp})
        tail = history[-keep:] if keep > 0 else history
        for m in tail:
            messages.append({"role": m.get("role", "user"), "content": m.get("content", "")})
        messages.append({"role": "user", "content": question})
        result = complete(provider_id, model_id, messages, config)

    # ============================
    # СТРАТЕГИЯ 3: Branching
    # ============================
    elif strategy == "branching":
        messages = [{"role": "system", "content": sys_prompt}]
        for m in history:
            messages.append({"role": m.get("role", "user"), "content": m.get("content", "")})
        messages.append({"role": "user", "content": question})
        result = complete(provider_id, model_id, messages, config)

    else:
        # fallback: полная история
        messages = [{"role": "system", "content": sys_prompt}]
        for m in history:
            messages.append({"role": m.get("role", "user"), "content": m.get("content", "")})
        messages.append({"role": "user", "content": question})
        result = complete(provider_id, model_id, messages, config)

    out = {
        "text": result["text"],
        "finish_reason": result["finish_reason"],
        "usage": result["usage"],
        "latency_ms": result["latency_ms"],
    }

    # Извлечение предложений для памяти
    memory = agent_data.get("memory") or {}
    suggestions, suggestion_raw = _suggest_memory(
        provider_id, model_id, question, result["text"], memory
    )
    if suggestions:
        out["memory_suggestions"] = suggestions
        if suggestion_raw and suggestion_raw.get("usage"):
            out["memory_suggest_usage"] = suggestion_raw["usage"]

    out.update(result_extra)
    if summary_usage:
        out["facts_update_usage"] = summary_usage

    # --- Машина состояний задачи ---
    current_state = agent_data.get("taskState") or dict(DEFAULT_TASK_STATE)
    classified, ts_result = _classify_task_state(
        provider_id, model_id, question, result["text"], current_state
    )
    out["taskStateDisplay"] = classified
    if classified.get("stage") == "Готовое решение":
        out["taskState"] = dict(DEFAULT_TASK_STATE)
    else:
        out["taskState"] = classified
    if ts_result and ts_result.get("usage"):
        out["task_state_usage"] = ts_result["usage"]

    # --- Тема чата (один раз, на первом обмене репликами) ---
    if not (agent_data.get("topic") or "").strip() and len(history) == 0:
        topic, topic_result = _extract_topic(provider_id, model_id, question, result["text"])
        if topic:
            out["topic"] = topic

    return out


def _extract_facts_with_usage(provider_id, model_id, question, facts_now):
    """extract_facts, возвращающая (facts_list, raw_complete_result)."""
    return _extract_facts(provider_id, model_id, question, facts_now), {}