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
    pick = (getattr(idea, "context", {}) or {}).get("strike_pick")
    intr = (getattr(idea, "context", {}) or {}).get("intraday")
    intraday_line = ""
    if intr and intr.get("ema_up_15m") is not None:
        want_up = idea.option_type == "CE"
        agrees = bool(intr["ema_up_15m"]) == want_up
        vw = "" if intr.get("above_vwap") is None else f"; price {'above' if intr['above_vwap'] else 'below'} the day average (VWAP)"
        intraday_line = (f"⏱ 15-min trend of the stock: {'agrees ✓' if agrees else 'AGAINST ⚠'} "
                         f"(EMA9 {'above' if intr['ema_up_15m'] else 'below'} EMA20{vw}) - the call itself is based on the DAILY trend\n\n")
    atm = (getattr(idea, "context", {}) or {}).get("atm_cost_per_lot")
    cheaper = (f"\u2139\ufe0f At-the-money costs ₹{atm:,.0f} per lot (over your limit), so this is a cheaper strike - "
               f"not tested over time yet.\n") if (pick and atm) else ""
    strike_line = (cheaper + "\U0001F9ED Strike chosen because: " + "; ".join(pick) + "\n\n") if pick else ""
    t3_line = (f"\U0001F3AF Target 3: ₹{idea.target3:.2f}\n"
               if getattr(idea, "n_targets", 3) >= 3 else "")
    return (
        f"\U0001F6A8\U0001F6A8 <b>ENTRY ALERT - BUY NOW</b> \U0001F6A8\U0001F6A8\n"
        f"\U0001F7E2 BUY {idea.symbol} {expiry_short} {fmt_strike(idea.strike)} {idea.option_type} "
        f"at ₹{idea.entry:.2f}\n"
        f"⏰ {_now_str()}\n\n"
        f"\U0001F3AF Target 1: ₹{idea.target1:.2f}\n"
        f"\U0001F3AF Target 2: ₹{idea.target2:.2f}\n"
        f"{t3_line}"
        f"\U0001F6D1 Stoploss: ₹{idea.stoploss:.2f}\n\n"
        f"\U0001F4E6 Lot size {idea.lot_size} | 1 lot costs ₹{idea.entry * idea.lot_size:,.0f} | "
        f"max loss at SL ≈ ₹{(idea.entry - idea.stoploss) * idea.lot_size:,.0f} per lot\n\n"
        f"\U0001F4CA Score: {idea.score:.0f}/100\n"
        f"\U0001F4C8 Est. success probability: {idea.win_probability:.0f}% "
        f"<i>(heuristic, not a guarantee)</i>\n\n"
        f"\U0001F4A1 Why: {reasons}\n\n"
        f"{intraday_line}"
        f"{strike_line}"
        f"⚡ Act now!"
    )


def format_exit_message(position, level: str, lots: int, price: float) -> str:
    """Short, action-first message for every fill of a paper trade. ALL amounts are PER LOT (one lot is sold at
    each target), so they are the same whether you hold 1, 2 or 3 lots. `position` is a paper_trader.PaperPosition;
    this is called AFTER the fill was recorded, so position.fills includes it."""
    expiry_short = _format_expiry_short(position.expiry)
    contract = (f"{position.symbol} {expiry_short} {fmt_strike(position.strike)} {position.option_type}")
    per_lot = round((price - position.entry) * position.lot_size, 2)       # this exit, per lot
    remaining = position.lots_remaining

    def money(x):
        return f"₹{abs(x):,.2f}"

    def result(x, noun="profit"):
        return f"Approx {noun} per lot - {money(x)}" if x >= 0 else f"Approx loss per lot - {money(x)}"

    if level == "SL":
        earlier_fills = position.fills[:-1]
        done = {f.level for f in earlier_fills}                       # targets already sold before this exit
        t1_done = position.t1_alerted or "T1" in done
        at_t1_price = position.stoploss >= position.target1           # stop trailed up to T1 (after T2)
        at_cost = (not at_t1_price) and position.stoploss >= position.entry and t1_done

        if at_t1_price:
            lines = [f"\U0001F6D1 <b>Stoploss hit at Target 1 price</b> - {contract}",
                     "Target 1 and Target 2 were already reached"]
        elif at_cost:
            lines = [f"\U0001F512 <b>Cost-to-cost price reached</b> - {contract}",
                     "Target 1 was already reached"]
        else:
            return "\n".join([f"\U0001F6D1 <b>Stoploss Hit</b> - {contract}", result(per_lot, "loss"),
                              "<b>Close the trade</b>"])

        # after a target: the profit booked (per lot, per target) and what the rest made (per lot)
        booked = [f"{f.level} {money(f.pnl / f.lots)}" for f in earlier_fills if f.lots and f.level in ("T1", "T2", "T3")]
        if booked:
            lines.append("Profit booked per lot - " + ", ".join(booked))
        else:
            lines.append("Nothing was sold at T1 (single lot) - that profit was only on paper")
        lines.append(result(per_lot) if abs(per_lot) >= 0.005 else "Remaining lots exited at about cost")
        lines.append("<b>Exit the trade</b>")
        return "\n".join(lines)

    if level == "TIME":
        return "\n".join([f"⏰ <b>Square-off time</b> - {contract}", result(per_lot), "<b>Close the trade</b>"])

    if level == "MANUAL":
        return f"\U0001F44B <b>Closed manually</b> - {contract}\n{result(per_lot)}"

    # ---- a target was hit ----
    last_level = f"T{getattr(position, 'n_targets', 3)}"
    if level == last_level or remaining <= 0:
        title = ("\U0001F3C1 <b>All targets hit</b>" if level == last_level
                 else f"\U0001F3AF <b>Target {level[1]} hit</b>")
        return "\n".join([f"{title} - {contract} at ₹{price:.2f}", result(per_lot), "<b>Close the trade</b>"])

    lines = [f"\U0001F3AF <b>Target {level[1]} hit</b> - {contract} at ₹{price:.2f}", result(per_lot)]
    if level == "T1" and config.MOVE_SL_TO_COST_AFTER_T1:
        lines.append(f"<b>Move remaining quantity to C2C price - ₹{position.entry:.2f}</b>")
    elif level == "T2" and getattr(position, "n_targets", 3) >= 3 and config.TRAIL_SL_TO_T1_AFTER_T2:
        lines.append(f"<b>Move remaining quantity to Target 1 price - ₹{position.target1:.2f}</b>")
    return "\n".join(lines)


def format_t1_message(position, price: float) -> str:
    """Sent when Target 1 is reached but NO lot is sold there (e.g. a 1-lot trade). When a lot is sold
    at T1, the exit message (format_exit_message) already says everything."""
    expiry_short = _format_expiry_short(position.expiry)
    contract = f"{position.symbol} {expiry_short} {fmt_strike(position.strike)} {position.option_type}"
    paper = round((price - position.entry) * position.lot_size, 2)
    lines = [f"\U0001F3AF <b>Target 1 hit</b> - {contract} at ₹{price:.2f}",
             f"Approx profit per lot (on paper) - {_money(paper)}"]
    if config.MOVE_SL_TO_COST_AFTER_T1:
        lines.append(f"<b>Move remaining quantity to C2C price - ₹{position.entry:.2f}</b>")
    return "\n".join(lines)


def _money(x: float) -> str:
    if abs(x) < 0.005:
        return "₹0.00"
    return f"{'+' if x > 0 else '-'}₹{abs(x):,.2f}"


money = _money


def _pct(pnl: float, capital: float) -> str:
    """Return on money invested, e.g. '+2.4%' (empty if nothing was invested)."""
    if not capital:
        return ""
    v = pnl / capital * 100
    return f"{'+' if v > 0 else ''}{v:.1f}%"


def _peak_capital(positions: list, per_trade_capital) -> float:
    """Largest amount of money tied up in open trades at the same moment (sweep over open/close times)."""
    import datetime as _dt
    events = []
    for p in positions:
        try:
            start = _dt.datetime.fromisoformat(p.opened_at)
            end = (_dt.datetime.fromisoformat(p.fills[-1].at)
                   if p.status == "CLOSED" and p.fills else _dt.datetime.now())
        except (ValueError, AttributeError):
            continue
        cap = per_trade_capital(p)
        events += [(start, cap), (end, -cap)]
    peak = cur = 0.0
    for _, delta in sorted(events, key=lambda e: (e[0], e[1])):   # closes before opens at the same instant
        cur += delta
        peak = max(peak, cur)
    return peak


def format_plan_check(positions: list, live_price) -> str:
    """The same trades played two ways, with the money side:
    A) 3 lots, scale-out (1 lot sold at each target) - what the bot follows
    B) 1 lot only, sold at Target 1.   Before brokerage and taxes."""
    if not positions:
        return ""
    ai = ar = bi = br = 0.0
    for p in positions:
        price = live_price(p) if p.status != "CLOSED" else None
        c, r, _ = p.plan_a(price)
        ai, ar = ai + c, ar + r
        c, r, _ = p.plan_b(price)
        bi, br = bi + c, br + r
    reached = sum(1 for p in positions if p.t1_price > 0)
    a_pnl, b_pnl = ar - ai, br - bi
    return (
        f"\U0001F4CA <b>Plan comparison</b> ({len(positions)} trades, {reached} reached T1; before charges)\n"
        f"A) full plan (index 3 lots, stock 2 lots; 1 sold at each target):\n"
        f"   invested ₹{ai:,.0f} → returned ₹{ar:,.0f} = <b>{_money(round(a_pnl, 2))}</b> ({_pct(a_pnl, ai)})\n"
        f"B) 1 lot, sold at T1:\n"
        f"   invested ₹{bi:,.0f} → returned ₹{br:,.0f} = <b>{_money(round(b_pnl, 2))}</b> ({_pct(b_pnl, bi)})\n"
        f"\U0001F4B5 Peak capital in use at one time: A ₹{_peak_capital(positions, lambda p: p.entry * p.lot_size * p.lots_total):,.0f}"
        f" | B ₹{_peak_capital(positions, lambda p: p.entry * p.lot_size):,.0f}"
    )


def format_trade_ledger(positions: list, live_price) -> str:
    """One block per trade: which targets it hit, how it ended, capital invested -> amount returned, P&L;
    then totals for the 3-lot plan. Open trades are valued at the latest price."""
    if not positions:
        return ""
    lines = ["\U0001F4D2 <b>Trades today</b>"]
    inv = ret = 0.0
    wins = losses = 0
    for p in positions:
        price = live_price(p) if p.status != "CLOSED" else None
        c, r, pnl = p.plan_a(price)
        inv, ret = inv + c, ret + r
        wins, losses = wins + (pnl > 0), losses + (pnl < 0)
        done = {f.level for f in p.fills}
        steps = ["T1 ✅" if (p.t1_alerted or "T1" in done) else "T1 ✗"]
        steps += [f"{lv} ✅" for lv in ("T2", "T3") if lv in done]
        if p.status == "CLOSED":
            last = p.fills[-1].level if p.fills else ""
            ending = {"SL": "stoploss hit", "TIME": "square-off", "MANUAL": "closed manually"}.get(last)
            if ending:
                where = ""
                if last == "SL":
                    where = (" (trailing stop at T1)" if p.stoploss >= p.target1
                             else " (stop was at cost)" if p.stoploss >= p.entry else "")
                steps.append(ending + where)
        else:
            steps.append("still open")
        icon = "✅" if pnl > 0 else ("❌" if pnl < 0 else "➖")
        lines.append(f"{icon} <b>{p.symbol} {fmt_strike(p.strike)} {p.option_type}</b> · "
                     f"{p.lots_total} lot × {p.lot_size}\n"
                     f"   {' → '.join(steps)}\n"
                     f"   ₹{c:,.0f} → ₹{r:,.0f} · <b>{_money(pnl)}</b> ({_pct(pnl, c)})")
    lines.append(
        f"\n━━━━━━━━━━━━━━\n"
        f"\U0001F4C5 <b>DAY TOTAL</b> - full plan (before brokerage and taxes)\n"
        f"Capital invested: ₹{inv:,.0f}\n"
        f"Amount returned: ₹{ret:,.0f}\n"
        f"<b>Profit/Loss: {_money(round(ret - inv, 2))}</b>  ({wins} profit, {losses} loss)\n"
        f"Return on the money invested: <b>{_pct(ret - inv, inv)}</b>\n"
        f"As % of your ₹{config.ACCOUNT_SIZE:,.0f} account: {_pct(ret - inv, config.ACCOUNT_SIZE)} "
        f"<i>(only meaningful if you could have taken these trades - see peak capital)</i>")
    return "\n".join(lines)


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
        all_levels = ("T1", "T2", "T3")[:getattr(p, "n_targets", 3)]
        nxt = next((lv for lv in all_levels if lv not in done), None)
        targets = {"T1": p.target1, "T2": p.target2, "T3": p.target3}
        if nxt is None:
            status = "all targets reached"
        elif not done:
            status = f"⏳ waiting for T1 (₹{p.target1:.2f})"
        else:
            status = (f"\U0001F3AF {'/'.join(lv for lv in all_levels if lv in done)} reached"
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
        names = {"SL": "stop", "TIME": "square-off", "MANUAL": "manual close"}
        how = ", then ".join(names.get(lv, lv) for lv in dict.fromkeys(f.level for f in p.fills)) or "-"
        closed_lines.append(f"{'✅' if booked >= 0 else '❌'} {p.symbol} {fmt_strike(p.strike)} "
                            f"{p.option_type} - exits: {how} - booked {_money(booked)}")

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
        + "\n\n" + format_plan_check(positions, live_price)
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


def _chunks(text: str, limit: int = 3900) -> list:
    """Split a long message at line boundaries (Telegram allows 4096 characters per message)."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts


def send_telegram_message(text: str) -> bool:
    token, chat_id = _get_credentials()
    if not token or not chat_id:
        print("[telegram] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set - skipping send. "
              "See README.md 'Telegram alerts'.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    ok = True
    for part in _chunks(text):
        try:
            resp = requests.post(url, data={
                "chat_id": chat_id,
                "text": part,
                "parse_mode": "HTML",
            }, timeout=10)
            resp.raise_for_status()
        except Exception as exc:
            print(f"[telegram] send failed: {exc}")
            ok = False
    return ok


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
