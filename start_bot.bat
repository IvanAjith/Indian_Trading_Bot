@echo off
REM start_bot.bat
REM -------------
REM Windows Command Prompt equivalent of start_bot.sh. Starts the bot in
REM its own minimized window (logging to monitor.log) so you can close this
REM Command Prompt window afterward without stopping the bot.
REM
REM NOTE: written without access to a real Windows machine to test on - if
REM anything here doesn't behave as expected, let Claude know so it can fix it.

cd /d "%~dp0"

echo Starting the bot in a new minimized window (logging to monitor.log)...
start "TradingBot" /min cmd /c "python -u main.py --mode monitor --paper --broker angelone > monitor.log 2>&1"

echo.
echo Bot started in a separate window titled "TradingBot" (minimized).
echo Watch its progress with:   type monitor.log
echo   (or just open monitor.log in Notepad)
echo Stop it with: stop_bot.bat
