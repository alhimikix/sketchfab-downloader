@echo off
set PYTHONUTF8=1
cd /d "%~dp0"
if exist "%~dp0dist\SketchfabLocal.exe" (
    start "" "%~dp0dist\SketchfabLocal.exe"
    exit /b 0
)
python -c "import playwright, numpy, PIL" >nul 2>&1
if errorlevel 1 (
    echo Installing local exporter dependencies...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        pause
        exit /b 1
    )
)
start "" pythonw.exe "%~dp0downloader_ui.py"
