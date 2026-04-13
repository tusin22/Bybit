@echo off
title Bybit Status
cd /d "%~dp0"
call .venv\Scripts\activate
python -m src.tools.status
pause
