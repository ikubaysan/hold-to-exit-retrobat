@echo off
setlocal

REM -----------------------------------------
REM Change to the directory of this script
REM -----------------------------------------
cd /d "%~dp0"

REM -----------------------------------------
REM Create venv if missing
REM -----------------------------------------
if not exist "venv" (
    echo Virtual environment not found. Creating venv...
    python -m venv venv
    if errorlevel 1 (
        echo Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo Installing requirements...
    call venv\Scripts\python.exe -m pip install --upgrade pip
    call venv\Scripts\python.exe -m pip install -r requirements.txt
)

REM -----------------------------------------
REM Activate the venv
REM -----------------------------------------
echo Activating virtual environment...
call venv\Scripts\activate

REM -----------------------------------------
REM Run your Python script
REM -----------------------------------------
echo Running main.py...
python main.py
echo Script finished.

pause
endlocal