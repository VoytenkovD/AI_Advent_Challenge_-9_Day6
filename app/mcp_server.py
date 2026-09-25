# -*- coding: utf-8 -*-
"""MCP-сервер: GitHub API + планировщик фоновых задач (Day 17–18).

Работает постоянно (транспорт streamable HTTP, http://127.0.0.1:5183/mcp).
Внутри — фоновый поток-планировщик, который раз в TICK_SEC секунд выполняет
задачи, у которых подошло время. Все данные хранятся в SQLite (app/data/scheduler.db).

Типы задач:
  • repo_watch — периодический сбор данных о репозитории (звёзды, форки, issues, новые коммиты)
  • reminder   — отложенное напоминание (однократно)
  • summary    — регулярная сводка: агрегирует собранные данные за период и публикует в ленту

Запуск отдельно (для работы 24/7):  python app/mcp_server.py
Веб-сервер агента (server.py) поднимает его сам, если он ещё не запущен.
"""

import json
import os
import sqlite3
import sys
import threading
import time
import traceback
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field

HOST = "127.0.0.1"
PORT = int(os.getenv("MCP_PORT", "5183"))
DB_PATH = Path(os.getenv("SCHEDULER_DB") or Path(__file__).resolve().parent / "data" / "scheduler.db")
GITHUB_API = "https://api.github.com"

TICK_SEC = 5                 # как часто планировщик проверяет задачи
MIN_WATCH_INTERVAL_MIN = 5   # GitHub без токена даёт 60 запросов/час — не опрашиваем чаще
MIN_SUMMARY_INTERVAL_MIN = 1

mcp = FastMCP(
    "github-scheduler", host=HOST, port=PORT, log_level="WARNING",
    stateless_http=True, json_response=True,
)


# ═════════════════════════════ хранилище (SQLite) ═════════════════════════════

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,              -- repo_watch | reminder | summary
    params       TEXT NOT NULL DEFAULT '{}', -- JSON
    interval_sec REAL,                       -- NULL = однократная задача
    next_run_at  REAL NOT NULL,
    created_at   REAL NOT NULL,
    last_run_at  REAL,
    last_status  TEXT,
    run_count    INTEGER NOT NULL DEFAULT 0,
    active       INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER,
    repo            TEXT NOT NULL,
    taken_at        REAL NOT NULL,
    stars           INTEGER, forks INTEGER, open_issues INTEGER,
    last_commit_sha TEXT,
    new_commits     INTEGER NOT NULL DEFAULT 0,
    commit_titles   TEXT NOT NULL DEFAULT '[]'  -- JSON: заголовки новых коммитов
);
CREATE INDEX IF NOT EXISTS ix_snapshots_repo_time ON snapshots(repo, taken_at);
CREATE TABLE IF NOT EXISTS feed (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    kind       TEXT NOT NULL,  -- summary | reminder | error
    title      TEXT NOT NULL,
    text       TEXT NOT NULL,
    data       TEXT            -- JSON с агрегатами (для summary)
);
"""


def _connect():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect()) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        con.commit()


def query(sql, args=()):
    with closing(_connect()) as con:
        return [dict(r) for r in con.execute(sql, args).fetchall()]


def execute(sql, args=()):
    with closing(_connect()) as con:
        cur = con.execute(sql, args)
        con.commit()
        return cur.lastrowid


def fmt_time(ts):
    return datetime.fromtimestamp(ts).strftime("%d.%m %H:%M") if ts else "—"


def iso_utc(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add_feed(kind, title, text, data=None):
    return execute(
        "INSERT INTO feed(created_at, kind, title, text, data) VALUES (?,?,?,?,?)",
        (time.time(), kind, title, text, json.dumps(data, ensure_ascii=False) if data else None),
    )


def job_view(job):
    params = json.loads(job["params"] or "{}")
    if job["kind"] == "repo_watch":
        what = f"Сбор данных {params['repo']} каждые {round(job['interval_sec'] / 60)} мин"
    elif job["kind"] == "reminder":
        what = f"Напоминание: {params['text']}"
    else:
        what = f"Сводка каждые {round(job['interval_sec'] / 60)} мин"
    return {
        "job_id": job["id"],
        "kind": job["kind"],
        "description": what,
        "active": bool(job["active"]),
        "next_run": fmt_time(job["next_run_at"]) if job["active"] else "—",
        "last_run": fmt_time(job["last_run_at"]),
        "last_status": job["last_status"],
        "run_count": job["run_count"],
    }


# ═════════════════════════════ GitHub API ═════════════════════════════

def _gh_headers():
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ai-advent-mcp-day18"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _gh_check(response, path):
    if response.status_code == 404:
        raise ValueError(f"Не найдено на GitHub: {path}")
    if response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0":
        raise RuntimeError("Превышен лимит запросов GitHub API. Задайте GITHUB_TOKEN или подождите.")
    response.raise_for_status()
    return response.json()


async def github_get(path, params=None):
    async with httpx.AsyncClient(base_url=GITHUB_API, headers=_gh_headers(), timeout=20) as client:
        return _gh_check(await client.get(path, params=params), path)


def github_get_sync(path, params=None):
    with httpx.Client(base_url=GITHUB_API, headers=_gh_headers(), timeout=20) as client:
        return _gh_check(client.get(path, params=params), path)


# ═════════════════════════════ исполнители задач ═════════════════════════════

def run_repo_watch(job, params):
    repo = params["repo"]
    info = github_get_sync(f"/repos/{repo}")

    prev = query("SELECT * FROM snapshots WHERE repo=? ORDER BY taken_at DESC LIMIT 1", (repo,))
    new_commits, titles, last_sha = 0, [], None
    if prev:
        commits = github_get_sync(
            f"/repos/{repo}/commits",
            {"since": iso_utc(prev[0]["taken_at"]), "per_page": 100},
        )
        new_commits = len(commits)
        titles = [c["commit"]["message"].splitlines()[0] for c in commits[:10]]
        last_sha = commits[0]["sha"][:7] if commits else prev[0]["last_commit_sha"]
    else:
        commits = github_get_sync(f"/repos/{repo}/commits", {"per_page": 1})
        last_sha = commits[0]["sha"][:7] if commits else None

    execute(
        "INSERT INTO snapshots(job_id, repo, taken_at, stars, forks, open_issues, last_commit_sha, "
        "new_commits, commit_titles) VALUES (?,?,?,?,?,?,?,?,?)",
        (job["id"], repo, time.time(), info["stargazers_count"], info["forks_count"],
         info["open_issues_count"], last_sha, new_commits, json.dumps(titles, ensure_ascii=False)),
    )
    return f"ok: ★{info['stargazers_count']}, новых коммитов {new_commits}"


def run_reminder(job, params):
    add_feed("reminder", "⏰ Напоминание", params["text"])
    return "ok: доставлено"


def run_summary(job, params):
    since = job["last_run_at"] or (time.time() - job["interval_sec"])
    summary = build_summary(since, time.time())
    add_feed("summary", summary["title"], summary["text"], summary["data"])
    return f"ok: репозиториев {len(summary['data']['repos'])}"


RUNNERS = {"repo_watch": run_repo_watch, "reminder": run_reminder, "summary": run_summary}


def run_due_jobs():
    now = time.time()
    for job in query("SELECT * FROM jobs WHERE active=1 AND next_run_at<=? ORDER BY next_run_at", (now,)):
        params = json.loads(job["params"] or "{}")
        try:
            status = RUNNERS[job["kind"]](job, params)
        except Exception as e:
            status = f"ошибка: {e}"
            add_feed("error", f"⚠️ Ошибка задачи #{job['id']}", f"{job_view(job)['description']}: {e}")
        if job["interval_sec"]:
            # следующее время считаем от момента запуска, чтобы не «догонять» пропущенные интервалы
            execute("UPDATE jobs SET next_run_at=?, last_run_at=?, last_status=?, run_count=run_count+1 WHERE id=?",
                    (now + job["interval_sec"], now, status, job["id"]))
        else:
            execute("UPDATE jobs SET active=0, last_run_at=?, last_status=?, run_count=run_count+1 WHERE id=?",
                    (now, status, job["id"]))


def scheduler_loop():
    while True:
        try:
            run_due_jobs()
        except Exception:
            traceback.print_exc()
        time.sleep(TICK_SEC)


# ═════════════════════════════ агрегирование ═════════════════════════════

def _signed(n):
    return f"+{n}" if n > 0 else str(n)


def build_summary(since, until):
    """Агрегирует снимки, напоминания и ошибки за период [since, until]."""
    repos = []
    for (repo,) in [(r["repo"],) for r in query(
            "SELECT DISTINCT repo FROM snapshots WHERE taken_at BETWEEN ? AND ?", (since, until))]:
        window = query("SELECT * FROM snapshots WHERE repo=? AND taken_at BETWEEN ? AND ? ORDER BY taken_at",
                       (repo, since, until))
        # база для сравнения — последний снимок ДО периода, иначе первый в периоде
        before = query("SELECT * FROM snapshots WHERE repo=? AND taken_at<? ORDER BY taken_at DESC LIMIT 1",
                       (repo, since))
        base = before[0] if before else window[0]
        counted = window if before else window[1:]
        last = window[-1]
        titles = [t for s in counted for t in json.loads(s["commit_titles"])]
        repos.append({
            "repo": repo,
            "snapshots": len(window),
            "stars": last["stars"], "stars_delta": last["stars"] - base["stars"],
            "forks": last["forks"], "forks_delta": last["forks"] - base["forks"],
            "open_issues": last["open_issues"], "open_issues_delta": last["open_issues"] - base["open_issues"],
            "new_commits": sum(s["new_commits"] for s in counted),
            "commit_titles": titles[:5],
        })

    reminders = query("SELECT created_at, text FROM feed WHERE kind='reminder' AND created_at BETWEEN ? AND ?",
                      (since, until))
    errors = query("SELECT COUNT(*) AS n FROM feed WHERE kind='error' AND created_at BETWEEN ? AND ?",
                   (since, until))[0]["n"]
    active_jobs = query("SELECT COUNT(*) AS n FROM jobs WHERE active=1")[0]["n"]

    title = f"📊 Сводка за {fmt_time(since)} – {fmt_time(until)}"
    lines = []
    if repos:
        for r in repos:
            lines.append(
                f"**{r['repo']}** — ★ {r['stars']} ({_signed(r['stars_delta'])}), "
                f"форки {r['forks']} ({_signed(r['forks_delta'])}), "
                f"issues {r['open_issues']} ({_signed(r['open_issues_delta'])}), "
                f"новых коммитов: {r['new_commits']}, замеров: {r['snapshots']}"
            )
            for t in r["commit_titles"]:
                lines.append(f"- {t}")
    else:
        lines.append("За период новых замеров нет.")
    if reminders:
        lines.append(f"\nНапоминаний сработало: {len(reminders)}")
        for rem in reminders:
            lines.append(f"- {fmt_time(rem['created_at'])}: {rem['text']}")
    if errors:
        lines.append(f"\nОшибок выполнения задач: {errors}")
    lines.append(f"\nАктивных задач в планировщике: {active_jobs}")

    data = {"since": since, "until": until, "repos": repos, "reminders": len(reminders),
            "errors": errors, "active_jobs": active_jobs}
    return {"title": title, "text": "\n".join(lines), "data": data}


# ═════════════════════════════ инструменты MCP ═════════════════════════════

Owner = Annotated[str, Field(description="Владелец репозитория (пользователь или организация), например 'pallets'")]
Repo = Annotated[str, Field(description="Название репозитория, например 'flask'")]


@mcp.tool()
async def github_repo_info(owner: Owner, repo: Repo) -> dict:
    """Получить сведения о публичном репозитории GitHub прямо сейчас: описание, звёзды, форки, язык, лицензию, даты."""
    data = await github_get(f"/repos/{owner}/{repo}")
    return {
        "full_name": data["full_name"], "description": data.get("description"), "url": data["html_url"],
        "stars": data["stargazers_count"], "forks": data["forks_count"],
        "open_issues": data["open_issues_count"], "language": data.get("language"),
        "license": (data.get("license") or {}).get("spdx_id"), "default_branch": data["default_branch"],
        "created_at": data["created_at"], "last_push_at": data["pushed_at"], "topics": data.get("topics", []),
    }


@mcp.tool()
async def github_recent_commits(
    owner: Owner, repo: Repo,
    limit: Annotated[int, Field(description="Сколько последних коммитов вернуть", ge=1, le=20)] = 5,
) -> dict:
    """Получить последние коммиты ветки по умолчанию: sha, автор, дата, заголовок сообщения."""
    data = await github_get(f"/repos/{owner}/{repo}/commits", params={"per_page": limit})
    commits = [{
        "sha": c["sha"][:7],
        "author": (c["commit"].get("author") or {}).get("name"),
        "date": (c["commit"].get("author") or {}).get("date"),
        "message": c["commit"]["message"].splitlines()[0],
    } for c in data]
    return {"repository": f"{owner}/{repo}", "count": len(commits), "commits": commits}


@mcp.tool()
def schedule_repo_watch(
    owner: Owner, repo: Repo,
    interval_minutes: Annotated[int, Field(
        description=f"Период сбора данных в минутах (не меньше {MIN_WATCH_INTERVAL_MIN})",
        ge=MIN_WATCH_INTERVAL_MIN, le=10080)] = 60,
) -> dict:
    """Поставить репозиторий на периодическое наблюдение: планировщик будет по расписанию собирать
    звёзды, форки, issues и новые коммиты и сохранять замеры в БД. Первый замер — сразу."""
    full = f"{owner}/{repo}"
    existing = query("SELECT * FROM jobs WHERE kind='repo_watch' AND active=1 AND json_extract(params,'$.repo')=?",
                     (full,))
    if existing:
        execute("UPDATE jobs SET interval_sec=? WHERE id=?", (interval_minutes * 60, existing[0]["id"]))
        job_id, note = existing[0]["id"], "Наблюдение уже было — обновлён интервал"
    else:
        now = time.time()
        job_id = execute(
            "INSERT INTO jobs(kind, params, interval_sec, next_run_at, created_at) VALUES ('repo_watch',?,?,?,?)",
            (json.dumps({"repo": full}), interval_minutes * 60, now, now))
        note = "Наблюдение создано, первый замер будет в течение нескольких секунд"
    return {"job_id": job_id, "note": note, "job": job_view(query("SELECT * FROM jobs WHERE id=?", (job_id,))[0])}


@mcp.tool()
def schedule_reminder(
    text: Annotated[str, Field(description="Текст напоминания")],
    in_minutes: Annotated[float, Field(description="Через сколько минут напомнить (можно дробное, 0.5 = 30 секунд)",
                                       gt=0, le=10080)],
) -> dict:
    """Создать отложенное напоминание: через указанное время оно появится в ленте сводок агента."""
    now = time.time()
    job_id = execute(
        "INSERT INTO jobs(kind, params, interval_sec, next_run_at, created_at) VALUES ('reminder',?,NULL,?,?)",
        (json.dumps({"text": text}, ensure_ascii=False), now + in_minutes * 60, now))
    return {"job_id": job_id, "fires_at": fmt_time(now + in_minutes * 60)}


@mcp.tool()
def schedule_summary(
    interval_minutes: Annotated[int, Field(
        description=f"Как часто публиковать сводку, в минутах (не меньше {MIN_SUMMARY_INTERVAL_MIN})",
        ge=MIN_SUMMARY_INTERVAL_MIN, le=10080)] = 60,
) -> dict:
    """Включить регулярную сводку: каждые N минут планировщик агрегирует собранные данные за прошедший
    период и публикует сводку в ленту. Предыдущее расписание сводки заменяется."""
    execute("UPDATE jobs SET active=0, last_status='заменена новым расписанием' WHERE kind='summary' AND active=1")
    now = time.time()
    job_id = execute(
        "INSERT INTO jobs(kind, params, interval_sec, next_run_at, created_at, last_run_at) "
        "VALUES ('summary','{}',?,?,?,?)",
        (interval_minutes * 60, now + interval_minutes * 60, now, now))
    return {"job_id": job_id, "first_summary_at": fmt_time(now + interval_minutes * 60)}


@mcp.tool()
def list_scheduled_jobs(
    include_inactive: Annotated[bool, Field(description="Показать также выполненные/отменённые задачи")] = False,
) -> dict:
    """Список задач планировщика: тип, описание, время следующего и последнего запуска, статус."""
    sql = "SELECT * FROM jobs" + ("" if include_inactive else " WHERE active=1") + " ORDER BY id DESC LIMIT 50"
    jobs = [job_view(j) for j in query(sql)]
    return {"count": len(jobs), "jobs": jobs}


@mcp.tool()
def cancel_scheduled_job(job_id: Annotated[int, Field(description="ID задачи из list_scheduled_jobs")]) -> dict:
    """Отменить задачу планировщика по её ID."""
    rows = query("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not rows:
        raise ValueError(f"Задача #{job_id} не найдена")
    execute("UPDATE jobs SET active=0, last_status='отменена' WHERE id=?", (job_id,))
    return {"job_id": job_id, "cancelled": True, "description": job_view(rows[0])["description"]}


@mcp.tool()
def get_summary(
    hours: Annotated[float, Field(description="За сколько последних часов агрегировать данные", gt=0, le=720)] = 24,
) -> dict:
    """Агрегированная сводка по собранным данным за последние N часов: изменение звёзд/форков/issues,
    число новых коммитов по каждому наблюдаемому репозиторию, сработавшие напоминания."""
    now = time.time()
    return build_summary(now - hours * 3600, now)


@mcp.tool()
def publish_summary_now() -> dict:
    """Сформировать сводку за период с момента предыдущей сводки и сразу опубликовать её в ленту."""
    last = query("SELECT created_at FROM feed WHERE kind='summary' ORDER BY id DESC LIMIT 1")
    since = last[0]["created_at"] if last else time.time() - 24 * 3600
    summary = build_summary(since, time.time())
    feed_id = add_feed("summary", summary["title"], summary["text"], summary["data"])
    return {"feed_id": feed_id, **summary}


@mcp.tool()
def get_feed(
    after_id: Annotated[int, Field(description="Вернуть записи с id больше этого (0 — последние)")] = 0,
    limit: Annotated[int, Field(description="Максимум записей", ge=1, le=100)] = 20,
) -> dict:
    """Лента планировщика: опубликованные сводки, сработавшие напоминания и ошибки (новые сверху)."""
    rows = query("SELECT id, created_at, kind, title, text FROM feed WHERE id>? ORDER BY id DESC LIMIT ?",
                 (after_id, limit))
    for r in rows:
        r["time"] = fmt_time(r["created_at"])
    last_id = query("SELECT COALESCE(MAX(id),0) AS m FROM feed")[0]["m"]
    return {"items": rows, "last_id": last_id}


if __name__ == "__main__":
    init_db()
    threading.Thread(target=scheduler_loop, name="scheduler", daemon=True).start()
    print(f"MCP-сервер с планировщиком: http://{HOST}:{PORT}/mcp  (БД: {DB_PATH})", file=sys.stderr, flush=True)
    mcp.run(transport="streamable-http")
