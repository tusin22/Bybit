@echo off
title Bybit Trade Bot - Dashboard
cd /d "%~dp0"
call .venv\Scripts\activate

echo.
echo ====================================================
echo   INICIANDO SERVIDOR WEB (DASHBOARD)
echo   Acesse no navegador: http://localhost:8050
echo ====================================================
echo.

:: Abre o navegador automaticamente redirecionando stderr para NUL (para não quebrar no Windows)
start http://localhost:8050 >nul 2>&1

:: Inicia a aplicação Flask
python -m src.tools.dashboard.app

pause
