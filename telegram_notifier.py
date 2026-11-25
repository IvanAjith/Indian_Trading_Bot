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


def fmt_strike(strike: float) -> str:
    """307.5 -> '307.5', 25800.0 -> '25800' (never round a half-rupee strike to a whole number)."""
    return f"{strike:.2f}".rstrip("0").rstrip(".")


def _now_str() -> str:
    return dt.datetime.now().strftime("%I:%M %p").lstrip("0")


def format_signal_message(idea) -> str:
    expiry_short = _format_expiry_short(idea.expiry)
    reasons = "; ".join(idea.reasons) if idea.reasons else "momentum setup"
    return (
        f"\U0001F6A8\U0001F6A8 <b>ENTRY ALERT - BUY NOW</b> \U0001F6A8\U0001F6A8\n"
        f"\U0001F7E2 BUY {idea.symbol} {expiry_short} {fmt_strike(idea.strike)} {idea.option_type} "
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
    """position is a paper_trader.PaperPosition. Sent whenever a fill happens (partial target booked,
    stoploss hit, or the time exit). Shows this fill AND how it fits into the whole trade (T1 reached,
    what was booked, what the stop was) so the final result is never a surprise."""
    expiry_short = _format_expiry_short(position.expiry)
    reason = _EXIT_REASON_LABELS.get(level, level)
    stop_at_cost = level == "SL" and position.stoploss >= position.entry
    if stop_at_cost:
        reason = "\U0001F512 Cost-to-cost stoploss hit"
    pnl = round((price - position.entry) * lots * position.lot_size, 2)
    pnl_emoji = "\U0001F4B0" if pnl >= 0 else "\U0001F4B8"
    remaining = position.lots_remaining
    total = round(sum(f.pnl for f in position.fills), 2)       # all fills so far, including this one
    earlier = round(total - pnl, 2)                            # booked before this exit

    story = []
    if position.t1_alerted:
        story.append("\U0001F3AF Target 1 was reached earlier"
                     + (" - the stoploss had moved to cost" if position.stoploss >= position.entry else ""))
    if position.peak_price > position.entry:
        story.append(f"Highest price seen ₹{position.peak_price:.2f} "
                     f"({(position.peak_price / position.entry - 1) * 100:+.0f}% from entry ₹{position.entry:.2f})")
    if position.lots_total == 1 and position.t1_alerted and level != "T1":
        story.append("1 lot cannot be sold in parts, so nothing was booked at T1 - that profit was only on paper")
    if stop_at_cost and pnl < 0:
        story.append("The stop is checked at each scan: price had already slipped below cost by then, "
                     "plus the assumed sell slippage")

    lines = [
        f"\U0001F51A <b>Exit Options Trade</b>",
        f"\U0001F534 SELL {position.symbol} {expiry_short} {fmt_strike(position.strike)} {position.option_type} "
        f"at ₹{price:.2f} ({reason})",
        f"⏰ {_now_str()}",
        f"{pnl_emoji} P&L this exit: ₹{pnl:+.2f}",
    ]
    if remaining <= 0:
        verdict = "PROFIT" if total > 0 else ("LOSS" if total < 0 else "BREAK-EVEN")
        lines.append(f"\n\U0001F4D2 <b>Whole trade</b>: booked earlier ₹{earlier:+.2f} + this exit ₹{pnl:+.2f}")
        lines += [f"• {t}" for t in story]
        lines.append(f"<b>FINAL RESULT: {verdict} ₹{total:+.2f}</b> (before brokerage and taxes)")
        lines.append("Position fully closed.")
    else:
        lines.append(f"{remaining} lot(s) still open, running toward the next target.")
        lines.append(f"Booked so far on this trade: ₹{total:+.2f}")
    return "\n".join(lines)


def format_t1_message(position, price: float) -> str:
    """Sent once when a position first reaches Target 1."""
    expiry_short = _format_expiry_short(position.expiry)
    gain_pct = (price - position.entry) / position.entry * 100 if position.entry else 0.0
    move_sl = config.MOVE_SL_TO_COST_AFTER_T1
    sl_line = (
        f"\U0001F512 <b>Move your stoploss to cost (cost-to-cost): ₹{position.entry:.2f}</b>\n"
        f"The rest of the trade can no longer turn into a big loss.\n"
        if move_sl else ""
    )
    return (
        f"\U0001F3AF <b>Target 1 reached</b>\n"
        f"{position.symbol} {expiry_short} {fmt_strike(position.strike)} {position.option_type}\n"
        f"⏰ {_now_str()}\n\n"
        f"Price ₹{price:.2f} ({gain_pct:+.0f}% from entry ₹{position.entry:.2f})\n\n"
        f"{sl_line}"
        f"Book partial profit if you hold more than one lot.\n"
        f"Still running toward: T2 ₹{position.target2:.2f} | T3 ₹{position.target3:.2f}"
    )


def _money(x: float) -> str:
    if abs(x) < 0.005:
        return "₹0.00"
    return f"{'+' if x > 0 else '-'}₹{abs(x):,.2f}"


def format_trades_summary(positions: list, live_price) -> str:
    """Body of the /trades reply. Every paper trade today with: profit/loss BOOKED so far (money
    already locked in by partial sales / exits), the OPEN profit/loss on what is still held, their
    net, and what it is waiting for next. Totals (profit, loss, net) are always at the bottom.
    live_price(position) -> latest premium or None."""
    if not positions:
        return "No paper trades yet today."
    open_positions = [p for p in positions if p.status != "CLOSED"]
    closed = [p for p in positions if p.status == "CLOSED"]

    blocks, nets = [], []          # one text block per trade; net P&L per trade (booked + open)
    booked_total = open_total = 0.0
    unpriced = 0

    def levels_reached(p):
        done = set(p.booked_levels)
        if p.t1_alerted:
            done.add("T1")
        return done

    for p in open_positions:
        booked = round(sum(f.pnl for f in p.fills), 2)
        price = live_price(p)
        if price is not None:
            open_pnl = round((price - p.entry) * p.lots_remaining * p.lot_size, 2)
            price_txt = f"Now ₹{price:.2f}"
        else:
            open_pnl, price_txt = 0.0, "Now: no fresh price"
            unpriced += 1
        net = round(booked + open_pnl, 2)
        booked_total += booked
        open_total += open_pnl
        nets.append(net)

        done = levels_reached(p)
        nxt = next((lv for lv in ("T1", "T2", "T3") if lv not in done), None)
        targets = {"T1": p.target1, "T2": p.target2, "T3": p.target3}
        if nxt is None:
            status = "all targets reached"
        elif not done:
            status = f"⏳ waiting for T1 (₹{p.target1:.2f})"
        else:
            status = (f"\U0001F3AF {'/'.join(lv for lv in ('T1', 'T2', 'T3') if lv in done)} reached"
                      f" - waiting for {nxt} (₹{targets[nxt]:.2f})")
        sl_note = " (at cost)" if p.stoploss >= p.entry else ""
        flag = " ⚠️oversize" if p.oversize else ""
        blocks.append(
            f"<b>{p.symbol} {fmt_strike(p.strike)} {p.option_type}</b>  {p.lots_remaining}/{p.lots_total} lots{flag}\n"
            f"Entry ₹{p.entry:.2f} | {price_txt}\n"
            f"\U0001F4B0 Booked {_money(booked)} | Open {_money(open_pnl)} | Net {_money(net)}\n"
            f"\U0001F6D1 SL ₹{p.stoploss:.2f}{sl_note}\n"
            f"{status}"
        )

    closed_lines = []
    for p in closed:
        booked = round(sum(f.pnl for f in p.fills), 2)
        booked_total += booked
        nets.append(booked)
        how = "/".join(dict.fromkeys(f.level for f in p.fills)) or "-"
        closed_lines.append(f"{'✅' if booked >= 0 else '❌'} {p.symbol} {fmt_strike(p.strike)} "
                            f"{p.option_type} - closed at {how} - booked {_money(booked)}")

    profit = round(sum(n for n in nets if n > 0), 2)
    loss = round(sum(n for n in nets if n < 0), 2)
    net_total = round(booked_total + open_total, 2)
    footer = (
        f"\n\n━━━━━━━━━━━━\n"
        f"✅ Total profit: {_money(profit)}  ({sum(1 for n in nets if n > 0)} trade(s))\n"
        f"❌ Total loss: {_money(loss)}  ({sum(1 for n in nets if n < 0)} trade(s))\n"
        f"\U0001F4B0 Booked so far: {_money(round(booked_total, 2))} | Open: {_money(round(open_total, 2))}\n"
        f"<b>NET: {_money(net_total)}</b>"
        + ("\n<i>(some open trades have no fresh price - counted as 0 open P&L)</i>" if unpriced else "")
    )
    header = (f"\U0001F4C2 <b>Paper trades today: {len(positions)}</b>  "
              f"({len(open_positions)} open, {len(closed)} closed)")

    parts = [header]
    if blocks:
        parts.append("\n<b>OPEN</b>")
    # Telegram caps a message at 4096 characters: if there are very many trades, drop the oldest
    # detail blocks but ALWAYS keep the totals at the bottom.
    shown = list(blocks)
    def assemble(blks):
        body = parts + [("\n" + b) for b in blks]
        if closed_lines:
            body += ["\n<b>CLOSED</b>"] + closed_lines
        return "\n".join(body) + footer
    text = assemble(shown)
    while len(text) > 3900 and shown:
        shown.pop()
        text = assemble(shown) + f"\n<i>... {len(blocks) - len(shown)} more open trade(s) not shown</i>"
    return text


def format_watchlist_message(result, leg=None) -> str:
    """Short heads-up: the CONTRACT to add to your watchlist (same style as the entry alert), so it
    can be told apart at a glance from the long ENTRY ALERT. `result` is a screener.ScreenResult;
    `leg` is the at-the-money option quote the bot would trade (strategy._pick_strike), if known."""
    bullish = result.bias == "BULLISH"
    arrow = "↑" if bullish else "↓"
    word = "Bullish" if bullish else "Bearish"
    option_type = "CE" if bullish else "PE"
    if leg is not None:
        contract = (f"{result.symbol} {_format_expiry_short(leg.expiry)} "
                    f"{fmt_strike(leg.strike)} {leg.option_type}")
        price = f" · ₹{leg.ltp:.2f}" if leg.ltp else ""
    else:   # contract not known (e.g. no option chain this scan): fall back to the underlying
        contract = f"{result.symbol} {option_type} (spot ₹{result.spot:,.2f})"
        price = ""
    return (
        f"\U0001F440 <b>WATCH ONLY {arrow} - add to watchlist</b>\n"
        f"<b>{contract}</b>{price}\n"
        f"{word}, score {result.score:.0f} - not an entry yet."
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
        f"{pos.symbol} {expiry_short} {fmt_strike(pos.strike)} {pos.option_type} - still open "
        f"({pos.lots_remaining} lot(s))\n"
        f"⏰ {_now_str()}\n\n"
        f"Entry: ₹{pos.entry:.2f}  |  Current: ₹{current_ltp:.2f}\n"
        f"{pnl_emoji} If closed now: ₹{pnl_if_closed:+.2f}\n\n"
        f"I'll square this off automatically at {config.FORCE_EXIT_TIME} (intraday only - "
        f"no overnight carry). No action needed."
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
