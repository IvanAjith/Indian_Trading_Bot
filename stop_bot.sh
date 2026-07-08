#!/bin/bash
# stop_bot.sh
# -----------
# Stops the background bot started by start_bot.sh.
#
#   ./stop_bot.sh

cd "$(dirname "$0")"

if [ ! -f bot.pid ]; then
    echo "No bot.pid file found - was the bot started with ./start_bot.sh?"
    exit 1
fi

PID=$(cat bot.pid)
if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    echo "Stopped bot (PID $PID)."
else
    echo "No running process found with PID $PID (already stopped?)."
fi
rm -f bot.pid
