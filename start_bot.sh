#!/bin/bash
# start_bot.sh
# ------------
# Starts the trading bot in the background so it keeps running even after
# you close this Terminal window. Run it once:
#
#   ./start_bot.sh
#
# Check on it any time with:
#
#   tail -f monitor.log        (see what it's doing right now)
#
# Stop it with:
#
#   ./stop_bot.sh
#
# NOTE: run this from a terminal where your Angel One env vars are already
# set (ANGELONE_API_KEY / ANGELONE_CLIENT_CODE / ANGELONE_PIN /
# ANGELONE_TOTP_SECRET) - e.g. a normal new Terminal window if you added
# them to ~/.zshrc. The background process inherits whatever is exported
# in THIS shell at the moment you run this script.

cd "$(dirname "$0")"

if [ -f bot.pid ] && kill -0 "$(cat bot.pid)" 2>/dev/null; then
    echo "Bot is already running (PID $(cat bot.pid))."
    echo "Run ./stop_bot.sh first if you want to restart it."
    exit 1
fi

nohup python3 -u main.py --mode monitor --paper --broker angelone > monitor.log 2>&1 &
echo $! > bot.pid
disown

echo "Bot started in the background (PID $(cat bot.pid))."
echo "Logs are going to monitor.log - watch them with: tail -f monitor.log"
echo "Stop it any time with: ./stop_bot.sh"
