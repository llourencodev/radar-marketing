@echo off
setlocal
cd /d "%~dp0"
title Radar de Marketing

if not exist ".venv\Scripts\python.exe" (
    echo Criando ambiente virtual...
    python -m venv .venv || py -m venv .venv
    if errorlevel 1 (
        echo Nao foi possivel criar o ambiente. Instale o Python 3.11 ou superior.
        pause
        exit /b 1
    )
)

echo Conferindo dependencias...
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo Falha ao instalar dependencias.
    pause
    exit /b 1
)

echo.
echo Radar de Marketing em http://127.0.0.1:8765  (Ctrl+C para encerrar)
echo.
".venv\Scripts\python.exe" -m app --open
pause
