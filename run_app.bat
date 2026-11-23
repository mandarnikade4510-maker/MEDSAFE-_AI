@echo off
title MedSafe AI
cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" (
    echo.
    echo [ERROR] The virtual environment "venv" was not found in this folder.
    echo Run these once, then double-click this file again:
    echo     python -m venv venv
    echo     venv\Scripts\activate.bat
    echo     pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

echo Starting MedSafe AI... your browser will open at http://localhost:8501
echo Close this window or press Ctrl+C to stop the app.
echo.
streamlit run streamlit_app.py --theme.base light --theme.primaryColor "#0E9AA7" --theme.backgroundColor "#F4F8FC" --theme.secondaryBackgroundColor "#E3EEF7" --theme.textColor "#1A2433"

pause
