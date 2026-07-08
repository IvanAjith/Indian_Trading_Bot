@echo off
REM stop_bot.bat
REM ------------
REM Stops the bot started by start_bot.bat, by closing its window.
REM
REM NOTE: written without access to a real Windows machine to test on - if
REM anything here doesn't behave as expected, let Claude know so it can fix it.

echo Stopping the bot (closing the "TradingBot" window, if running)...
taskkill /FI "WINDOWTITLE eq TradingBot*" /T /F >nul 2>&1

echo Done. (If it wasn't running, that's fine too.)
