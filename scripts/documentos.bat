@echo off
rem Base de conhecimento: pasta, urls.txt e espacos.txt (documentacao do Refined). Roda 1x por dia.
cd /d "%~dp0.."
if not exist logs mkdir logs
set PYTHONUTF8=1
echo ===== %date% %time% >> logs\documentos.log
uv run python -m copilot_suporte.documentos >> logs\documentos.log 2>&1
