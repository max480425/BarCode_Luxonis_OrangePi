@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

echo ======================================================================
echo   Luxonis OAK-1-FF — System Detekcji Kodow Kreskowych i Partii Puszek
echo ======================================================================

if not exist "%~dp0.venv\Scripts\python.exe" (
    echo [BLAD] Nie znaleziono srodowiska wirtualnego: %~dp0.venv
    echo Uruchom: python -m uv venv .venv ^&^& python -m uv pip install -r requirements.txt --python .venv\Scripts\python.exe
    pause
    exit /b 1
)

echo Uruchamianie aplikacji...
"%~dp0.venv\Scripts\python.exe" -m src.app

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [UWAGA] Program zakonczyl dzialanie z kodem bledu: %ERRORLEVEL%
    pause
)
