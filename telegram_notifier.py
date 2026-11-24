"""
telegram_notifier.py
--------------------
Sends a formatted alert to your Telegram when a new signal fires. Uses
Telegram's plain HTTP Bot API - no extra library needed, just `requests`.

Setup (see README.md "Telegram alerts" for the full walkthrough):
  1. Create a bot via @BotFather in Telegram, get a token.
  2. Message your new bot once, then fetch your chat_id.
  3. Put both in config.py (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID) or set
     the TB_TELEGRAM_TOKEN / TB_TELEGRAM_CHAT_ID environment variables.
  4. Set config.TELEGRAM_ENABLED = True.

This file never places trades - it only sends you a text message.
"""

import os
import datetime as dt
import requests

import config

_LEVEL_LABELS = {
    "T1": "Target 1", "T2": "Target 2", "T3": "Target 3",
    "SL": "Stoploss", "TIME": "Time exit (square-off)",
}
_LEVEL_EMOJI = {
    "T1": "✅", "T2": "✅", "T3": "\U0001F3C1",
    "SL": "\U0001F6D1", "TIME": "⏰",
}


def _get_credentials() -> tuple[str, str]:
    token = os.environ.get("TB_TELEGRAM_TOKEN") or config.TELEGRAM_BOT_TOKEN
    chat_id = os.environ.get("TB_TELEGRAM_CHAT_ID") or config.TELEGRAM_CHAT_ID
    return token.strip(), chat_id.strip()   # strip hidden newline/space from pasted secrets


def _format_expiry_short(expiry: str) -> str:
    """Angel One gives '28JUL2026', NSE/mock give '2026-07-09' - normalize
    either to 'DD MMM' (e.g. '28 JUL') to match the requested message style."""
    for fmt in ("%d%b%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(expiry, fmt).date().strftime("%d %b").upper()
        except ValueError:
            continue
    return expiry  # unrecognized format - show as-is rather than crash


def _now_str() -> str:
    return dt.datetime.now().strftime("%I:%M %p").lstrip("0")


def format_signal_message(idea) -> str:
    expiry_short = _format_expiry_short(idea.expiry)
    reasons = "; ".join(idea.reasons) if idea.reasons else "momentum setup"
    return (
        f"\U0001F195 <b>New Options Trade</b>\n"
        f"\U0001F7E2 BUY {idea.symbol} {expiry_short} {idea.strike:.0f} {idea.option_type} "
        f"at ₹{idea.entry:.2f}\n"
        f"⏰ {_now_str()}\n\n"
        f"\U0001F3AF Target 1: ₹{idea.target1:.2f}\n"
        f"\U0001F3AF Target 2: ₹{idea.target2:.2f}\n"
        f"\U0001F3AF Target 3: ₹{idea.target3:.2f}\n"
        f"\U0001F6D1 Stoploss: ₹{idea.stoploss:.2f}\n\n"
        f"\U0001F4E6 Lot size {idea.lot_size} | 1 lot costs ₹{idea.entry * idea.lot_size:,.0f} | "
        f"max loss at SL ≈ ₹{(idea.entry - idea.stoploss) * idea.lot_size:,.0f} per lot\n\n"
        f"\U0001F4CA Score: {idea.score:.0f}/100\n"
        f"\U0001F4C8 Est. success probability: {idea.win_probability:.0f}% "
        f"<i>(heuristic, not a guarantee)</i>\n\n"
        f"\U0001F4A1 Why: {reasons}\n\n"
        f"⚡ Act now!"
    )


_EXIT_REASON_LABELS = {
    "T1": "\U0001F3AF Target 1 Hit",
    "T2": "\U0001F3AF Target 2 Hit",
    "T3": "\U0001F3C1 Target 3 Hit",
    "SL": "\U0001F6D1 Stop Loss Hit",
    "TIME": "⏰ Time Exit (Square-off)",
    "MANUAL": "\U0001F44B Manually Closed",
}


def format_exit_message(position, level: str, lots: int, price: float) -> str:
    """position is a paper_trader.PaperPosition. Sent whenever a fill happens
    (partial target booked, stoploss hit, or the 3:15pm time exit)."""
    expiry_short = _format_expiry_short(position.expiry)
    reason = _EXIT_REASON_LABELS.get(level, level)
    pnl = round((price - position.entry) * lots * position.lot_size, 2)
    pnl_emoji = "\U0001F4B0" if pnl >= 0 else "\U0001F4B8"
    remaining = position.lots_remaining
    status_line = (
        "Position fully closed." if remaining <= 0
        else f"{remaining} lot(s) still open, running toward the next target."
    )
    return (
        f"\U0001F51A <b>Exit Options Trade</b>\n"
        f"\U0001F534 SELL {position.symbol} {expiry_short} {position.strike:.0f} {position.option_type} "
        f"at ₹{price:.2f} ({reason})\n"
        f"⏰ {_now_str()}\n"
        f"{pnl_emoji} P&L this fill: ₹{pnl:+.2f}\n"
        f"{status_line}"
    )


def format_watchlist_message(result) -> str:
    """Lighter-weight than format_signal_message - for a symbol with a real
    directional bias that hasn't cleared MIN_SIGNAL_SCORE yet. `result` is a
    screener.ScreenResult - this is deliberately about the UNDERLYING only
    (no strike/premium/expiry), since which strike ends up relevant depends
    on the actual entry price at whatever moment a real signal fires later -
    not worth surfacing here. Check the option chain yourself if you want to
    look, or just wait for the full alert."""
    bullish = result.bias == "BULLISH"
    direction_label = "Bullish" if bullish else "Bearish"
    option_hint = "CE" if bullish else "PE"
    reasons = "; ".join(result.trigger_reasons) if result.trigger_reasons else "early momentum building"
    return (
        f"\U0001F440 <b>{result.symbol} - Building {direction_label} Momentum</b>\n"
        f"⏰ {_now_str()}\n\n"
        f"Spot: ₹{result.spot:.2f}\n"
        f"\U0001F4CA Score: {result.score:.0f}/100 "
        f"<i>(below the {config.MIN_SIGNAL_SCORE:.0f} buy threshold - not a signal yet)</i>\n\n"
        f"\U0001F4A1 So far: {reasons}\n\n"
        f"May be building toward a {option_hint} buy signal - check the option chain "
        f"yourself if you want to look, or wait for the full 🆕 alert if/when it fires."
    )


def format_eod_check_message(pos, current_ltp: float) -> str:
    """Sent config.EOD_WARNING_MINUTES_BEFORE_CLOSE minutes before
    FORCE_EXIT_TIME for any position still open, so you can decide to hold
    it overnight instead of letting the automatic square-off happen."""
    expiry_short = _format_expiry_short(pos.expiry)
    pnl_if_closed = round((current_ltp - pos.entry) * pos.lots_remaining * pos.lot_size, 2)
    pnl_emoji = "\U0001F4B0" if pnl_if_closed >= 0 else "\U0001F4B8"
    return (
        f"⏳ <b>End-of-day check-in</b>\n"
        f"{pos.symbol} {expiry_short} {pos.strike:.0f} {pos.option_type} - still open "
        f"({pos.lots_remaining} lot(s))\n"
        f"⏰ {_now_str()}\n\n"
        f"Entry: ₹{pos.entry:.2f}  |  Current: ₹{current_ltp:.2f}\n"
        f"{pnl_emoji} If closed now: ₹{pnl_if_closed:+.2f}\n\n"
        f"I'll auto square-off at {config.FORCE_EXIT_TIME} unless you reply:\n"
        f"<b>hold {pos.symbol}</b> - keep it open past today's close (your call, your risk)\n\n"
        f"Otherwise no action needed - I'll close it automatically."
    )


def send_telegram_message(text: str) -> bool:
    token, chat_id = _get_credentials()
    if not token or not chat_id:
        print("[telegram] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set - skipping send. "
              "See README.md 'Telegram alerts'.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(url, data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as exc:
        print(f"[telegram] send failed: {exc}")
        return False


def poll_status_requests(offset, providers: dict) -> int:
    """Checks for new incoming Telegram messages once (non-blocking) and
    replies to recognized commands. `providers` maps a command word (no
    leading slash, e.g. "status", "summary", "hold", "close") to a callable
    taking ONE string argument - the rest of the message after the command
    word, or "" if there wasn't one - and returning the reply text. E.g.
    sending "hold PEL" calls providers["hold"]("PEL"); sending "status"
    calls providers["status"]("").  Both "status" and "/status" (etc.) are
    recognized. Only replies to messages from YOUR configured chat_id -
    anything else is ignored.

    Returns the new offset to pass in next time (so already-seen messages
    aren't re-processed). Call this repeatedly from the monitor loop, e.g.
    every 15-20 seconds, not just once per full market scan.
    """
    token, chat_id = _get_credentials()
    if not token or not chat_id:
        return offset

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {"timeout": 0}
    if offset is not None:
        params["offset"] = offset
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"[telegram] status poll failed (not fatal): {exc}")
        return offset

    new_offset = offset
    for update in data.get("result", []):
        new_offset = update["update_id"] + 1
        message = update.get("message", {})
        raw_text = (message.get("text") or "").strip()
        from_chat = str(message.get("chat", {}).get("id", ""))
        if from_chat != str(chat_id) or not raw_text:
            continue  # ignore anyone other than the configured chat
        parts = raw_text.lstrip("/").split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        provider = providers.get(cmd)
        if provider:
            send_telegram_message(provider(arg))
    return new_offset


def notify_new_signals(ideas: list) -> int:
    if not config.TELEGRAM_ENABLED:
        return 0
    sent = 0
    for idea in ideas:
        if send_telegram_message(format_signal_message(idea)):
            sent += 1
    return sent


def send_test_message() -> bool:
    """Run `python telegram_notifier.py` to fire a one-off test message and
    confirm your token/chat_id are wired up correctly."""
    return send_telegram_message(
        "✅ Trading bot Telegram alerts are wired up correctly. "
        "You'll get a message like this whenever a new signal fires."
    )


if __name__ == "__main__":
    ok = send_test_message()
    print("Test message sent." if ok else "Test message failed - check TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID.")
