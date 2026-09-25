# -*- coding: utf-8 -*-
"""Сценарии оркестрации MCP и проверка флоу (Day 20).

Сценарий = запрос пользователя + ожидания:
  servers  — серверы, которые обязаны быть задействованы
  tools    — инструменты («сервер__инструмент»), которые обязаны быть вызваны
  order    — пары [A, B]: хотя бы один вызов B должен идти ПОСЛЕ первого вызова A
  forbid   — инструменты, которые вызывать нельзя (например, лишние записи)

check_flow() сверяет лог вызовов оркестратора (mcp_calls) с ожиданиями и
дополнительно проверяет маршрутизацию: каждый вызов ушёл на сервер из префикса
своего имени и завершился без ошибки.
"""

SCENARIOS = [
    {
        "id": "report",
        "title": "Отчёт: GitHub + погода → заметка",
        "prompt": (
            "Найди 3 самых популярных репозитория по запросу «mcp server language:python», "
            "узнай текущую погоду в Москве, затем сохрани заметку «Отчёт дня» с кратким списком "
            "этих репозиториев (название и звёзды) и погодой, после этого прочитай заметку и "
            "подтверди, что всё записалось."
        ),
        "expect": {
            "servers": ["github", "weather", "notes"],
            "tools": ["github__search_github_repos", "weather__get_current_weather",
                      "notes__save_note", "notes__read_note"],
            "order": [["github__search_github_repos", "notes__save_note"],
                      ["weather__get_current_weather", "notes__save_note"],
                      ["notes__save_note", "notes__read_note"]],
        },
    },
    {
        "id": "long",
        "title": "Длинный флоу: сравнение, прогноз, заметка, напоминание",
        "prompt": (
            "Сравни репозитории pallets/flask и fastapi/fastapi: звёзды и по 2 последних коммита у каждого. "
            "Посмотри прогноз погоды в Санкт-Петербурге на 3 дня. Создай заметку «Сравнение фреймворков» "
            "с итогами сравнения, потом допиши в неё отдельным абзацем прогноз погоды. "
            "Поставь напоминание через 30 минут «Перечитать сравнение фреймворков». "
            "В конце покажи список моих заметок."
        ),
        "expect": {
            "servers": ["github", "weather", "notes"],
            "tools": ["github__github_repo_info", "github__github_recent_commits", "weather__get_forecast",
                      "notes__save_note", "notes__append_to_note", "github__schedule_reminder",
                      "notes__list_notes"],
            "order": [["github__github_repo_info", "notes__save_note"],
                      ["github__github_recent_commits", "notes__save_note"],
                      ["notes__save_note", "notes__append_to_note"],
                      ["weather__get_forecast", "notes__append_to_note"],
                      ["notes__append_to_note", "notes__list_notes"]],
            "min_calls": {"github__github_repo_info": 2, "github__github_recent_commits": 2},
        },
    },
]


def get_scenario(scenario_id):
    for s in SCENARIOS:
        if s["id"] == scenario_id:
            return s
    raise ValueError("Сценарий не найден: {}".format(scenario_id))


def check_flow(calls, expect):
    """Возвращает список проверок [{name, ok, detail}] и общий итог."""
    names = [c["name"] for c in calls]
    checks = []

    def add(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    # 1. Маршрутизация: сервер вызова совпадает с префиксом имени, ошибок нет
    bad_route = [c["name"] for c in calls if not c.get("server") or not c["name"].startswith(c["server"] + "__")]
    add("маршрутизация: каждый вызов ушёл на свой сервер", not bad_route,
        "ошибки: " + ", ".join(bad_route) if bad_route else "{} вызовов".format(len(calls)))
    errors = ["#{} {}".format(c["n"], c["name"]) for c in calls if c.get("isError")]
    add("вызовы без ошибок", not errors, ", ".join(errors))

    # 2. Задействованы все нужные серверы
    used = sorted({c["server"] for c in calls if c.get("server")})
    for s in expect.get("servers", []):
        add("сервер {} задействован".format(s), s in used)

    # 3. Выбраны нужные инструменты (и нужное число раз)
    for t in expect.get("tools", []):
        add("вызван {}".format(t), t in names, "{} раз".format(names.count(t)) if t in names else "не вызывался")
    for t, n in (expect.get("min_calls") or {}).items():
        add("{} вызван ≥ {} раз".format(t, n), names.count(t) >= n, "{} раз".format(names.count(t)))
    for t in expect.get("forbid", []):
        add("не вызывался {}".format(t), t not in names)

    # 4. Порядок: B идёт после первого A
    for a, b in expect.get("order", []):
        if a in names and b in names:
            first_a = names.index(a)
            last_b = len(names) - 1 - names[::-1].index(b)
            ok = last_b > first_a
            detail = "#{} {} → #{} {}".format(first_a + 1, a.split("__")[1], last_b + 1, b.split("__")[1])
        else:
            ok, detail = False, "нет одного из вызовов"
        add("порядок: {} → {}".format(a.split("__")[1], b.split("__")[1]), ok, detail)

    return {"ok": all(c["ok"] for c in checks), "passed": sum(c["ok"] for c in checks),
            "total": len(checks), "checks": checks, "servers_used": used}
