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
# NOTE: run this from a terminal where your credentials are already set as
# env vars (ANGELONE_API_KEY / ANGELONE_CLIENT_CODE / ANGELONE_PIN /
# ANGELONE_TOTP_SECRET / TB_TELEGRAM_TOKEN / TB_TELEGRAM_CHAT_ID) - e.g. a
# normal new Terminal window if you added them to ~/.zshrc. The background
# process inherits whatever is exported in THIS shell at the moment you run
# this script. Env vars work the same regardless of (base)/(venv) - only
# which Python interpreter is used (below) depends on that.
#
# This script always uses this project's OWN dedicated virtual environment
# (./venv) rather than whatever happens to be active in your terminal - so
# it doesn't matter if your prompt shows (base) or some other project's
# (venv) when you run this. One-time setup, if you haven't already:
#   python3 -m venv venv
#   source venv/bin/activate
#   pip install -r requirements.txt

cd "$(dirname "$0")"

if [ ! -x "venv/bin/python3" ]; then
    echo "This project's dedicated virtual environment doesn't exist yet."
    echo "Run these three commands once to set it up, then try again:"
    echo "  python3 -m venv venv"
    echo "  source venv/bin/activate"
    echo "  pip install -r requirements.txt"
    exit 1
fi

if [ -f bot.pid ] && kill -0 "$(cat bot.pid)" 2>/dev/null; then
    echo "Bot is already running (PID $(cat bot.pid))."
    echo "Run ./stop_bot.sh first if you want to restart it."
    exit 1
fi

nohup venv/bin/python3 -u main.py --mode monitor --paper --broker angelone > monitor.log 2>&1 &
echo $! > bot.pid
disown

echo "Bot started in the background (PID $(cat bot.pid)), using ./venv."
echo "Logs are going to monitor.log - watch them with: tail -f monitor.log"
echo "Stop it any time with: ./stop_bot.sh"
