@echo off
rem MCP-сервер с планировщиком отдельно от веб-интерфейса (для работы 24/7).
rem Веб-сервер (run.bat) подключится к нему автоматически.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python "%~dp0app\mcp_server.py"
pause
