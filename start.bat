@echo off
title Bybit Trade Bot
echo ========================================
echo   Bybit Trade Bot - Iniciando...
echo ========================================
echo.

cd /d "%~dp0"
call .venv\Scripts\activate
python -m src.main

echo.
echo Bot encerrado.
pause
