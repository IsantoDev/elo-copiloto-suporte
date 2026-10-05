@echo off
rem Sobe o Elo em http://127.0.0.1:8000 (o Ollama precisa estar aberto: ele inicia com o Windows).
cd /d "%~dp0.."
if not exist logs mkdir logs
set PYTHONUTF8=1
uv run python -m copilot_suporte.api
