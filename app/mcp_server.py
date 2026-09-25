"""MCP-сервер вокруг GitHub REST API (транспорт stdio).

Инструменты:
  • github_repo_info       — сведения о репозитории (звёзды, язык, описание, лицензия…)
  • github_recent_commits  — последние коммиты репозитория

Работает без токена (лимит GitHub — 60 запросов/час с одного IP).
Если задать переменную окружения GITHUB_TOKEN, лимит вырастет до 5000/час.
"""

import os
from typing import Annotated

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field

GITHUB_API = "https://api.github.com"

mcp = FastMCP("github-server", log_level="WARNING")


async def github_get(path: str, params: dict | None = None) -> dict | list:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ai-advent-mcp-day17",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(base_url=GITHUB_API, headers=headers, timeout=20) as client:
        response = await client.get(path, params=params)

    if response.status_code == 404:
        raise ValueError(f"Не найдено на GitHub: {path}")
    if response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0":
        raise RuntimeError("Превышен лимит запросов GitHub API. Задайте GITHUB_TOKEN или подождите.")
    response.raise_for_status()
    return response.json()


# ── Инструмент 1 ─────────────────────────────────────────────────────────────
@mcp.tool()
async def github_repo_info(
    owner: Annotated[str, Field(description="Владелец репозитория (пользователь или организация), например 'python'")],
    repo: Annotated[str, Field(description="Название репозитория, например 'cpython'")],
) -> dict:
    """Получить сведения о публичном репозитории GitHub: описание, звёзды, форки, язык, лицензию, даты."""
    data = await github_get(f"/repos/{owner}/{repo}")
    return {
        "full_name": data["full_name"],
        "description": data.get("description"),
        "url": data["html_url"],
        "stars": data["stargazers_count"],
        "forks": data["forks_count"],
        "open_issues": data["open_issues_count"],
        "language": data.get("language"),
        "license": (data.get("license") or {}).get("spdx_id"),
        "default_branch": data["default_branch"],
        "created_at": data["created_at"],
        "last_push_at": data["pushed_at"],
        "topics": data.get("topics", []),
    }


# ── Инструмент 2 ─────────────────────────────────────────────────────────────
@mcp.tool()
async def github_recent_commits(
    owner: Annotated[str, Field(description="Владелец репозитория (пользователь или организация)")],
    repo: Annotated[str, Field(description="Название репозитория")],
    limit: Annotated[int, Field(description="Сколько последних коммитов вернуть", ge=1, le=20)] = 5,
) -> dict:
    """Получить последние коммиты ветки по умолчанию: sha, автор, дата, заголовок сообщения."""
    data = await github_get(f"/repos/{owner}/{repo}/commits", params={"per_page": limit})
    commits = [
        {
            "sha": c["sha"][:7],
            "author": (c["commit"].get("author") or {}).get("name"),
            "date": (c["commit"].get("author") or {}).get("date"),
            "message": c["commit"]["message"].splitlines()[0],
        }
        for c in data
    ]
    return {"repository": f"{owner}/{repo}", "count": len(commits), "commits": commits}


if __name__ == "__main__":
    mcp.run()  # транспорт stdio
