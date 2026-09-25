# -*- coding: utf-8 -*-
"""MCP-сервер «notes»: заметки в Markdown-файлах (app/data/notes). Транспорт stdio.

Инструменты:
  • save_note(title, content)     — создать/перезаписать заметку
  • append_to_note(title, text)   — дописать в конец заметки
  • read_note(title)              — прочитать заметку
  • list_notes()                  — список заметок
"""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

NOTES_DIR = Path(os.getenv("NOTES_DIR") or Path(__file__).resolve().parent / "data" / "notes")

mcp = FastMCP("notes", log_level="WARNING")

Title = Annotated[str, Field(description="Название заметки, например 'Отчёт по MCP'")]


def note_path(title: str) -> Path:
    slug = re.sub(r"[^\w\- ]", "_", title.strip(), flags=re.UNICODE).strip(" _")[:80]
    if not slug:
        raise ValueError("Пустое название заметки")
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    return NOTES_DIR / f"{slug}.md"


def info(path: Path) -> dict:
    st = path.stat()
    return {"title": path.stem, "file": path.name, "chars": len(path.read_text(encoding="utf-8")),
            "modified": datetime.fromtimestamp(st.st_mtime).strftime("%d.%m.%Y %H:%M:%S")}


@mcp.tool()
def save_note(title: Title, content: Annotated[str, Field(description="Текст заметки (Markdown)")]) -> dict:
    """Создать заметку или полностью перезаписать существующую."""
    path = note_path(title)
    existed = path.exists()
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return {"saved": True, "overwritten": existed, **info(path)}


@mcp.tool()
def append_to_note(title: Title, text: Annotated[str, Field(description="Текст, который дописать в конец")]) -> dict:
    """Дописать текст в конец заметки (если заметки нет — она будет создана)."""
    path = note_path(title)
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text((old.rstrip() + "\n\n" if old else "") + text.rstrip() + "\n", encoding="utf-8")
    return {"appended": True, **info(path)}


@mcp.tool()
def read_note(title: Title) -> dict:
    """Прочитать заметку целиком."""
    path = note_path(title)
    if not path.exists():
        raise ValueError(f"Заметка «{title}» не найдена")
    return {**info(path), "content": path.read_text(encoding="utf-8")}


@mcp.tool()
def list_notes() -> dict:
    """Список всех заметок (новые сверху)."""
    if not NOTES_DIR.is_dir():
        return {"count": 0, "notes": []}
    files = sorted(NOTES_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return {"count": len(files), "notes": [info(p) for p in files]}


if __name__ == "__main__":
    mcp.run()
