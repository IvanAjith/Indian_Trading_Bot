@echo off
REM start_bot.bat
REM -------------
REM Windows Command Prompt equivalent of start_bot.sh. Starts the bot in
REM its own minimized window (logging to monitor.log) so you can close this
REM Command Prompt window afterward without stopping the bot.
REM
REM Always uses this project's OWN dedicated virtual environment (.\venv)
REM rather than whatever's active in Command Prompt. One-time setup, if you
REM haven't already (run these three commands once in this folder):
REM   python -m venv venv
REM   venv\Scripts\activate
REM   pip install -r requirements.txt
REM
REM NOTE: written without access to a real Windows machine to test on - if
REM anything here doesn't behave as expected, let Claude know so it can fix it.

cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo This project's dedicated virtual environment doesn't exist yet.
    echo Run these three commands once to set it up, then try again:
    echo   python -m venv venv
    echo   venv\Scripts\activate
    echo   pip install -r requirements.txt
    exit /b 1
)

echo Starting the bot in a new minimized window (logging to monitor.log)...
start "TradingBot" /min cmd /c "venv\Scripts\python.exe -u main.py --mode monitor --paper --broker angelone > monitor.log 2>&1"

echo.
echo Bot started in a separate window titled "TradingBot" (minimized), using .\venv.
echo Watch its progress with:   type monitor.log
echo   (or just open monitor.log in Notepad)
echo Stop it with: stop_bot.bat
