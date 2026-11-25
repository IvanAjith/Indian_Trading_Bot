"""
main.py
-------
Orchestrator. Three modes:

  python main.py --mode watchlist   # scan universe, print/save ranked ideas
  python main.py --mode paper       # scan + open paper trades + one MTM pass
  python main.py --mode backtest    # run backtest.py over each symbol

This is designed to be triggered by your OS scheduler (cron / Task
Scheduler), NOT run inside this sandbox - it needs real network access to
NSE or your broker, and market-hours awareness. See README.md.
"""

import argparse
import datetime as dt
import threading
import time

import config
from data_fetcher import get_provider
from screener import rank_universe, watchlist_candidates
from strategy import build_trade_idea, _pick_strike
from paper_trader import PaperTradingAccount
from telegram_notifier import (
    notify_new_signals, send_telegram_message, format_exit_message, poll_status_requests,
    format_watchlist_message, format_eod_check_message, format_t1_message, format_trades_summary, fmt_strike,
)
import backtest as bt


def _minus_minutes(hhmm: str, minutes: int) -> str:
    t = dt.datetime.strptime(hhmm, "%H:%M") - dt.timedelta(minutes=minutes)
    return t.strftime("%H:%M")


CONSECUTIVE_FAILURE_LIMIT = 5  # abort the run instead of grinding through
                               # the whole universe once the feed is clearly down


def _default_universe():
    return [(s, True) for s in config.INDEX_UNIVERSE] + [(s, False) for s in config.STOCK_UNIVERSE]


def fetch_all_snapshots(provider):
    universe = provider.get_universe() if hasattr(provider, "get_universe") else _default_universe()
    # A stock/index with an open trade must ALWAYS be scanned, even if it dropped out of today's ranking.
    scanned = {s for s, _ in universe}
    for sym in sorted({c[0] for c in getattr(provider, "watch_contracts", ())} - scanned):
        universe = universe + [(sym, sym in config.INDEX_UNIVERSE)]

    # Fast path: providers that can fetch many symbols per API call (Angel One).
    if hasattr(provider, "get_snapshots"):
        try:
            return provider.get_snapshots(universe)
        except Exception as exc:
            print(f"[main] batch fetch failed ({exc}); falling back to one symbol at a time.")

    snapshots = []
    consecutive_failures = 0
    for sym, is_index in universe:
        try:
            snapshots.append(provider.get_underlying_snapshot(sym, is_index=is_index))
            consecutive_failures = 0
        except Exception as exc:
            print(f"[main] skip {sym}: {exc}")
            consecutive_failures += 1
            if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                print(
                    f"\n[main] {consecutive_failures} symbols in a row failed - the data "
                    f"feed is down, not just one flaky symbol. Stopping early instead of "
                    f"grinding through the rest.\n"
                    f"  -> Run `python3 diagnose_angelone.py` to check the Angel One login/data calls.\n"
                )
                break
    return snapshots


def run_watchlist(provider):
    snapshots = fetch_all_snapshots(provider)
    ranked = rank_universe(snapshots)
    snap_by_symbol = {s.symbol: s for s in snapshots}

    print(f"\n=== Trade ideas: {dt.datetime.now():%Y-%m-%d %H:%M} ===")
    ideas = []
    for r in ranked:
        idea = build_trade_idea(snap_by_symbol[r.symbol], r)
        if idea:
            ideas.append(idea)
            print(
                f"{idea.symbol:12s} {idea.option_type} {fmt_strike(idea.strike):>8s} "
                f"exp {idea.expiry} | entry {idea.entry:>7.2f} "
                f"T1 {idea.target1:>7.2f} T2 {idea.target2:>7.2f} T3 {idea.target3:>7.2f} "
                f"sl {idea.stoploss:>7.2f} | score {idea.score}\n"
                f"{'':14s} why: {idea.rationale}"
            )
    if not ideas:
        print("No qualifying setups today (this is normal and expected often).")

    sent = notify_new_signals(ideas)
    if config.TELEGRAM_ENABLED:
        print(f"[telegram] sent {sent}/{len(ideas)} alert(s).")
    return ideas


def run_paper(provider):
    account = PaperTradingAccount()
    ideas = run_watchlist(provider)
    for idea in (ideas if config.PAPER_TRACK_ALL_SIGNALS else ideas[:config.MAX_TRADES_PER_DAY]):
        pos = account.open_trade(idea)
        if pos is None:
            continue
        print(
            f"[paper] opened {pos.lots_total} lot(s) of {pos.symbol} {pos.option_type} "
            f"{pos.strike} @ {pos.entry}  (booking plan: {pos.lots_by_level})"
        )
    print("[paper] summary:", account.summary())
    print(
        "[paper] This single run only opens positions and exits once - use "
        "`--mode monitor --paper` instead for continuous intraday tracking."
    )


def _within_market_hours(now: dt.datetime) -> tuple[bool, bool]:
    """Returns (is_before_open, is_after_close)."""
    hhmm = now.strftime("%H:%M")
    return hhmm < config.MARKET_OPEN_TIME, hhmm >= config.MARKET_CLOSE_TIME


def _fresh_day_log() -> dict:
    return {"signals": [], "fills": [], "watchlist": []}


def find_leg(snap, pos):
    """Latest quote for a held position: same expiry preferred, searched in the near-the-money
    chain and in the extra quotes fetched for held contracts that drifted out of that window."""
    if not snap:
        return None
    quotes = list(snap.option_chain) + list(getattr(snap, "held_quotes", []))
    same = [q for q in quotes if q.strike == pos.strike and q.option_type == pos.option_type]
    return next((q for q in same if q.expiry == pos.expiry), None) or (same[0] if same else None)


def run_monitor(provider, use_paper: bool = False, max_cycles: int = None, session_end: str = None,
                skip_if_closed: bool = False):
    """Runs continuously, re-scanning every config.MONITOR_INTERVAL_SECONDS
    while the market is open. Unlike --mode watchlist (a single snapshot),
    this only alerts on setups NOT already alerted on today, so you get a
    stream of genuinely new ideas through the day instead of one burst at a
    fixed time.

    Unlike earlier versions, the process does NOT exit at market close - it
    stays alive 24/7 (idling, not scanning, outside 09:00-15:30) purely so
    it can keep answering Telegram "status"/"summary" pings any time you
    send them. Stop it yourself with Ctrl+C when you actually want it down.

    Sends a Telegram message the moment a target/stoploss actually fires
    (via PaperTradingAccount's on_fill callback). Telegram commands supported:
      status  -> what the bot is doing right now (scanning / idle / outside
                 market hours)
      summary -> a recap of everything that happened TODAY (signals fired,
                 watchlist alerts, fills/exits, running P&L). Resets at
                 midnight; stops answering with today's data after 23:58
                 since the day is about to roll over.
      hold SYMBOL   -> (--paper only) skip today's automatic square-off for
                       that open position - you're taking on overnight risk
                       yourself. Resets back to auto-exit the NEXT day.
      close SYMBOL [price]  -> (--paper only) manually record that position
                       as closed (e.g. you exited it yourself via your
                       broker) - frees up trade-room and keeps the day's
                       summary accurate. Uses the latest known price if you
                       don't give one.

    Also sends two other kinds of proactive alerts:
      - A lighter-weight "watchlist" alert (screener.watchlist_candidates)
        for symbols with real directional bias that haven't yet cleared the
        full buy-signal score floor - since scans repeat every
        MONITOR_INTERVAL_SECONDS, this can give you a few minutes' heads up
        before (if ever) the real signal fires.
      - An end-of-day check-in (config.EOD_WARNING_MINUTES_BEFORE_CLOSE
        minutes before FORCE_EXIT_TIME) for any still-open position, showing
        current P&L and explaining the hold/close commands above.

    max_cycles is for testing (--mock) only - leave None for real use.

    session_end ("HH:MM", IST): if given, the run ends itself at that time -
    open paper positions are closed at the latest price, a summary is sent
    to Telegram, and the function returns. This is how the GitHub Actions
    deployment works (a bounded session, not a 24/7 process).

    skip_if_closed: before scanning, wait until 09:20 IST, then confirm via live
    data that NSE actually traded today (provider.traded_today()). If it did not
    (exchange holiday), send one Telegram message and end the run - no stale-price
    signals and no wasted minutes. If the check can't be answered, carry on.
    """
    state = {
        "last_scan_at": None, "alerted_today": set(), "last_signal_count": 0,
        "day_log": _fresh_day_log(), "watchlisted_today": set(),
        "eod_warned_today": set(), "last_snap_by_symbol": {},
        "scans": 0, "symbols_scanned": 0, "scan_secs": [], "suppressed_logged": set(), "closed_checked": not skip_if_closed,
        "watchlist_score_history": {},  # {(symbol, bias): last-seen score} -
                                        # used to only alert when a setup is
                                        # actually strengthening, not just
                                        # sitting anywhere in the watch band.
        "signaled_directions_today": set(),  # {(symbol, bias)} - stops a watchlist
                                              # alert firing for a direction that
                                              # already got a full buy signal today.
    }
    eod_warning_time = _minus_minutes(config.FORCE_EXIT_TIME, config.EOD_WARNING_MINUTES_BEFORE_CLOSE)

    def _in_market_hours(now: dt.datetime) -> bool:
        hhmm = now.strftime("%H:%M")
        return config.MARKET_OPEN_TIME <= hhmm < config.MARKET_CLOSE_TIME

    def _status_text(_arg: str = "") -> str:
        now = dt.datetime.now()
        if not _in_market_hours(now):
            return (
                f"\U0001F4A4 Outside market hours "
                f"({config.MARKET_OPEN_TIME}–{config.MARKET_CLOSE_TIME} IST).\n"
                f"Not scanning right now - I'll resume at {config.MARKET_OPEN_TIME}. "
                f"Bot process is alive, just idle.\n"
                f"(Send 'summary' for a recap of today so far.)"
            )
        if state["last_scan_at"] is None:
            return "🤖 Bot is up, about to start scanning."
        base = (
            f"🤖 Bot is running. Last scan: {state['last_scan_at']:%H:%M}.\n"
            f"{len(state['alerted_today'])} signal(s) sent today"
            f"{' (' + str(state['last_signal_count']) + ' new this scan)' if state['last_signal_count'] else ''}.\n"
        )
        if state["alerted_today"]:
            base += "Currently tracking active setups, still scanning for more."
        else:
            base += "No active signals. Scanning the market for potential moves."
        return base

    def _summary_text(_arg: str = "") -> str:
        now = dt.datetime.now()
        if now.strftime("%H:%M") >= "23:58":
            return "🌙 Almost midnight - today's summary window has closed. Ask again once the next trading day starts."
        log = state["day_log"]
        signals, fills, watchlist = log["signals"], log["fills"], log["watchlist"]
        lines = [f"\U0001F4CB <b>Summary for {now:%d %b %Y}</b>"]
        if not signals and not fills and not watchlist:
            lines.append("Nothing to report yet today.")
            return "\n".join(lines)

        if watchlist:
            lines.append(f"\n\U0001F440 Watchlist alerts: {len(watchlist)}")
            for w in watchlist:
                lines.append(f"  • {w['time']} {w.get('contract') or (w['symbol'] + ' ' + w['option_type'])} "
                             f"(score {w['score']:.0f})")

        lines.append(f"\n\U0001F195 Signals sent: {len(signals)}")
        for s in signals:
            lines.append(
                f"  • {s['time']} {s['symbol']} {s['option_type']} {fmt_strike(s['strike'])} "
                f"@ ₹{s['entry']:.2f} (score {s['score']:.0f}, ~{s['win_probability']:.0f}% est.)"
            )

        if fills:
            total_pnl = round(sum(f["pnl"] for f in fills), 2)
            lines.append(f"\n\U0001F51A Exits/fills: {len(fills)}")
            for f in fills:
                lines.append(
                    f"  • {f['time']} {f['symbol']} {f['option_type']} {fmt_strike(f['strike'])} "
                    f"{f['level']} @ ₹{f['price']:.2f} (P&L ₹{f['pnl']:+.2f})"
                )
            pnl_emoji = "\U0001F4B0" if total_pnl >= 0 else "\U0001F4B8"
            lines.append(f"\n{pnl_emoji} Today's P&L: ₹{total_pnl:+.2f}")
        else:
            lines.append("\nNo exits/fills yet today.")
        return "\n".join(lines)

    def _handle_hold(arg: str) -> str:
        if session_end:
            return ("Overnight carry isn't supported - the bot runs one bounded session per day and "
                    f"squares everything off at {config.FORCE_EXIT_TIME}. Use 'close SYMBOL' to log a manual exit.")
        if not account:
            return "Paper trading isn't enabled for this run (start with --paper), so there's nothing to hold."
        symbol = arg.strip().upper()
        open_positions = [p for p in account.open_positions if p.status != "CLOSED"]
        if not symbol:
            if not open_positions:
                return "No open positions right now - nothing to hold."
            syms = ", ".join(sorted({p.symbol for p in open_positions}))
            return f"Which one? Reply 'hold SYMBOL'. Currently open: {syms}"
        matches = [p for p in open_positions if p.symbol == symbol]
        if not matches:
            return f"No open position found for {symbol}."
        for p in matches:
            p.held_over = True
        return (
            f"\U0001F91D Got it - {symbol} won't be auto-closed at {config.FORCE_EXIT_TIME} today. "
            f"You're choosing to carry it overnight yourself - profit/loss from here is on you. "
            f"Send 'close {symbol}' any time to record your manual exit."
        )

    def _handle_close(arg: str) -> str:
        if not account:
            return "Paper trading isn't enabled for this run (start with --paper), so there's nothing to close."
        parts = arg.strip().split()
        open_positions = [p for p in account.open_positions if p.status != "CLOSED"]
        if not parts:
            if not open_positions:
                return "No open positions right now."
            syms = ", ".join(sorted({p.symbol for p in open_positions}))
            return f"Which one? Reply 'close SYMBOL' or 'close SYMBOL PRICE'. Currently open: {syms}"
        symbol = parts[0].upper()
        manual_price = None
        if len(parts) > 1:
            try:
                manual_price = float(parts[1])
            except ValueError:
                return f"Couldn't read '{parts[1]}' as a price. Try: close {symbol} 250"
        matches = [p for p in open_positions if p.symbol == symbol]
        if not matches:
            return f"No open position found for {symbol}."
        pos = matches[0]
        price = manual_price
        if price is None:
            snap = state["last_snap_by_symbol"].get(symbol)
            leg = find_leg(snap, pos)
            if not leg:
                return (f"Don't have a fresh price for {symbol} to close it automatically. "
                        f"Tell me the price you got: close {symbol} PRICE")
            price = leg.ltp
        account.manual_close(pos, price)
        return f"\U0001F44B Recorded {symbol} closed at ₹{price:.2f}. Freed up for new trades."

    def _active_block_reason(symbol: str, option_type: str = None):
        """Why a NEW alert for this underlying must be suppressed, or None. Direction does NOT
        matter: while a CE *or* PE trade on this stock/index is still running you get no further
        alert (no panic-reversals); you wait for that trade's target or stoploss.
        'open'     -> a trade on this underlying is still running
        'cooldown' -> one closed less than REENTRY_COOLDOWN_MIN minutes ago"""
        if not account or not config.ONE_ACTIVE_PER_UNDERLYING:
            return None
        for p in account.open_positions:
            if p.symbol != symbol:
                continue
            if p.status != "CLOSED":
                return "open"
            if config.REENTRY_COOLDOWN_MIN and p.fills:
                try:
                    last = dt.datetime.fromisoformat(p.fills[-1].at)
                except ValueError:
                    continue
                if dt.datetime.now() - last < dt.timedelta(minutes=config.REENTRY_COOLDOWN_MIN):
                    return "cooldown"
        return None

    def _live_price(pos):
        snap = state["last_snap_by_symbol"].get(pos.symbol)
        leg = find_leg(snap, pos)
        return leg.ltp if leg else None

    def _handle_add(arg: str) -> str:
        """/add SYMBOL STRIKE CE|PE ENTRY [LOTS] - track a trade you took yourself."""
        usage = "Usage: /add SYMBOL STRIKE CE|PE ENTRY [LOTS]   e.g. /add NIFTY 25800 CE 249.87"
        if not account:
            return "Paper trading isn't enabled for this run, so I can't track trades."
        parts = arg.split()
        if len(parts) not in (4, 5):
            return usage
        symbol, option_type = parts[0].upper(), parts[2].upper()
        try:
            strike, entry = float(parts[1]), float(parts[3])
            lots = int(parts[4]) if len(parts) == 5 else None
        except ValueError:
            return usage
        if option_type not in ("CE", "PE") or entry <= 0 or (lots is not None and lots <= 0):
            return usage
        info = provider.contract_info(symbol, strike, option_type) if hasattr(provider, "contract_info") else None
        if info is None:   # no instrument list (e.g. mock feed): use the latest scan if it saw the contract
            snap = state["last_snap_by_symbol"].get(symbol)
            leg = next((q for q in (snap.option_chain if snap else [])
                        if q.strike == strike and q.option_type == option_type), None)
            info = (leg.expiry, leg.lot_size, leg.strike) if leg else None
        if info is None:
            return (f"I can't find {symbol} {fmt_strike(strike)} {option_type} (nearest expiry). "
                    f"Check the symbol/strike (index/stock name as in the alerts).")
        expiry, lot_size, strike = info   # strike = the real contract strike (e.g. 307.5 for '308')
        if any(p.symbol == symbol and p.strike == strike and p.option_type == option_type
               and p.status != "CLOSED" for p in account.open_positions):
            return f"{symbol} {fmt_strike(strike)} {option_type} is already being tracked (see /trades)."
        pos = account.open_manual(symbol, option_type, strike, entry, expiry, lot_size, lots)
        return (
            f"✅ <b>Tracking {symbol} {expiry} {fmt_strike(strike)} {option_type}</b>\n"
            f"Entry ₹{pos.entry:.2f} | {pos.lots_total} lot(s) x {lot_size}\n"
            f"SL ₹{pos.stoploss:.2f} | T1 ₹{pos.target1:.2f} T2 ₹{pos.target2:.2f} T3 ₹{pos.target3:.2f}\n"
            f"I'll alert you on T1 / exits and no new {symbol} alerts will come while it is open."
        )

    def _handle_help(_arg: str = "") -> str:
        return (
            "\U0001F4CB <b>Commands</b>\n"
            "/help - this list\n"
            "/status - is the bot scanning right now\n"
            "/trades - open paper trades with live P&L\n"
            "/pnl - profit and loss (booked + open)\n"
            "/summary - today's signals, exits and P&L\n"
            "/add SYMBOL STRIKE CE|PE ENTRY [LOTS] - track a trade you took yourself\n"
            "/close SYMBOL [price] - record a position as closed\n"
            "<i>I only answer while a session is running (09:15 IST onward).</i>"
        )

    def _handle_trades(_arg: str = "") -> str:
        if not account:
            return "Paper trading isn't enabled for this run, so there are no trades to show."
        return format_trades_summary(account.open_positions, _live_price)

    def _handle_pnl(_arg: str = "") -> str:
        if not account:
            return "Paper trading isn't enabled for this run, so there's no P&L to show."
        s = account.summary()
        unreal, priced = 0.0, 0
        for p in account.open_positions:
            if p.status == "CLOSED":
                continue
            price = _live_price(p)
            if price is not None:
                unreal += (price - p.entry) * p.lots_remaining * p.lot_size
                priced += 1
        realized = s["total_pnl"]
        total = realized + unreal
        emoji = "\U0001F4B0" if total >= 0 else "\U0001F4B8"
        return (
            f"{emoji} <b>P&L (paper)</b>\n"
            f"Booked: ₹{realized:+.2f}\n"
            f"Open (unrealised): ₹{unreal:+.2f}"
            f"{'' if priced == s['open_or_partial'] else ' (some positions have no fresh price)'}\n"
            f"Total: ₹{total:+.2f}\n\n"
            f"Closed trades: {s['positions_closed']} (wins {s['wins']}, win rate {s['win_rate_pct']}%)\n"
            f"Paper capital: ₹{s['capital']:.2f}\n"
            f"<i>Includes {config.PAPER_SLIPPAGE_PCT}% slippage per side, real lot sizes.</i>"
        )

    def _on_fill(pos, level, lots, price):
        state["day_log"]["fills"].append({
            "time": dt.datetime.now().strftime("%H:%M"),
            "symbol": pos.symbol, "option_type": pos.option_type, "strike": pos.strike,
            "level": level, "lots": lots, "price": price,
            "pnl": round((price - pos.entry) * lots * pos.lot_size, 2),
        })
        if config.TELEGRAM_ENABLED:
            send_telegram_message(format_exit_message(pos, level, lots, price))

    def _on_t1(pos, price):
        if config.TELEGRAM_ENABLED:
            send_telegram_message(format_t1_message(pos, price))

    account = PaperTradingAccount(on_fill=_on_fill, on_t1=_on_t1) if use_paper else None
    today = dt.date.today()
    cycles = 0
    telegram_offset = None
    telegram_commands = {
        "status": _status_text, "summary": _summary_text,
        "hold": _handle_hold, "close": _handle_close,
        "help": _handle_help, "start": _handle_help, "add": _handle_add,
        "trades": _handle_trades, "pnl": _handle_pnl,
    }

    def _sleep_with_status_polling(total_seconds: float):
        nonlocal telegram_offset
        elapsed = 0.0
        step = config.STATUS_POLL_INTERVAL_SECONDS
        while elapsed < total_seconds:
            this_step = min(step, total_seconds - elapsed)
            time.sleep(this_step)
            elapsed += this_step
            if config.TELEGRAM_ENABLED:
                telegram_offset = poll_status_requests(telegram_offset, telegram_commands)

    print(f"[monitor] Starting ({'paper' if use_paper else 'watchlist-only'} mode). "
          f"Market hours {config.MARKET_OPEN_TIME}-{config.MARKET_CLOSE_TIME} IST. "
          f"Runs 24/7 (idling outside market hours) until you Ctrl+C.")

    feed_down_cycles = 0
    feed_alert_sent = False
    if session_end and dt.datetime.now().strftime("%H:%M") >= session_end and config.TELEGRAM_ENABLED:
        send_telegram_message(
            f"⚠️ Session end time {session_end} (24-hour clock) is already past - "
            f"it is {dt.datetime.now():%H:%M} IST, so this run will end immediately. "
            f"For an evening test use e.g. 22:45, not 10:45."
        )
    if config.TELEGRAM_ENABLED:
        send_telegram_message(
            f"\U0001F7E2 <b>Bot started</b> {dt.datetime.now():%d %b %H:%M} IST\n"
            f"Scanning every ~{config.MONITOR_INTERVAL_SECONDS}s (plus scan time)"
            f"{' until ' + session_end + ' IST (24-hour clock)' if session_end else ''}. "
            f"Signals at score {config.MIN_SIGNAL_SCORE:.0f}+.\n"
            f"First scan takes a few minutes (it loads history for every symbol); "
            f"you can already send /add or /trades meanwhile."
        )

    def _fetch_while_polling():
        """Run one market scan in a background thread and keep answering Telegram commands every
        few seconds while it runs. The first scan of a session downloads history for every symbol
        and can take several minutes - commands (/add, /trades, ...) must not sit unanswered."""
        nonlocal telegram_offset
        box = {}

        def work():
            try:
                box["snaps"] = fetch_all_snapshots(provider)
            except Exception as exc:   # surfaced in the main thread below
                box["error"] = exc

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        while worker.is_alive():
            worker.join(timeout=config.STATUS_POLL_INTERVAL_SECONDS)
            if worker.is_alive() and config.TELEGRAM_ENABLED:
                telegram_offset = poll_status_requests(telegram_offset, telegram_commands)
        if "error" in box:
            raise box["error"]
        return box["snaps"]

    def _timing_line() -> str:
        secs = state["scan_secs"]
        if not secs:
            return ""
        steady = secs[1:] or secs
        line = (f"\nScan time: first {secs[0] / 60:.1f} min, then avg {sum(steady) / len(steady):.0f}s "
                f"(slowest {max(steady):.0f}s).")
        stats = getattr(provider, "stats", None)
        if stats:
            line += f" API calls {stats['calls']}, rate-limit retries {stats['rate_limit_retries']}."
        return line

    def _end_session():
        if account:
            def _ltp(pos):
                snap = state["last_snap_by_symbol"].get(pos.symbol)
                leg = find_leg(snap, pos)
                return leg.ltp if leg else None
            closed = account.close_all(_ltp)
            print(f"[monitor] session end - closed {closed} open paper position(s).")
        if config.TELEGRAM_ENABLED:
            send_telegram_message(
                "\U0001F51A <b>Session ended.</b>\n" + _summary_text() +
                f"\n\n\U0001F916 Bot health: {state['scans']} scans completed, "
                f"{state['symbols_scanned']} symbols per scan." + _timing_line())

    try:
        while True:
            now = dt.datetime.now()
            if session_end and now.strftime("%H:%M") >= session_end:
                _end_session()
                break
            if now.date() != today:
                state["alerted_today"] = set()
                state["watchlisted_today"] = set()
                state["eod_warned_today"] = set()
                state["watchlist_score_history"] = {}
                state["signaled_directions_today"] = set()
                state["suppressed_logged"] = set()
                state["day_log"] = _fresh_day_log()
                if account:
                    # Holding is a same-day-only override - each new day starts
                    # back at the default (auto square-off at FORCE_EXIT_TIME)
                    # unless you explicitly say "hold SYMBOL" again that day.
                    for p in account.open_positions:
                        if p.status != "CLOSED":
                            p.held_over = False
                today = now.date()

            if not _in_market_hours(now):
                before_open, _ = _within_market_hours(now)
                reason = f"opens at {config.MARKET_OPEN_TIME}" if before_open else "closed for the day"
                print(f"[monitor] {now:%H:%M} - market {reason}. Idling (still answering Telegram status/summary)...")
                _sleep_with_status_polling(min(config.MONITOR_INTERVAL_SECONDS, 60))
                cycles += 1
                if max_cycles and cycles >= max_cycles:
                    break
                continue

            if not state["closed_checked"]:
                if now.strftime("%H:%M") < "09:20":
                    _sleep_with_status_polling(min(30, config.MONITOR_INTERVAL_SECONDS + 20))
                    continue
                traded = None
                for attempt in range(3):
                    traded = provider.traded_today() if hasattr(provider, "traded_today") else None
                    if traded is not False:
                        break
                    if attempt < 2:
                        print("[monitor] no trading data for today yet - re-checking in 60s...")
                        _sleep_with_status_polling(60)
                state["closed_checked"] = True
                if traded is False:
                    print("[monitor] NSE did not trade today (holiday) - ending the run.")
                    if config.TELEGRAM_ENABLED:
                        send_telegram_message(
                            f"\U0001F4C5 <b>Market closed today</b> ({dt.date.today():%A, %d %b}).\n"
                            f"No trading data for today, so the bot is standing down - no signals today.")
                    break
                print("[monitor] market is trading today - scanning.")

            if account is not None and provider is not None:   # make sure held contracts keep being priced even if they
                                      # drift away from the at-the-money strike
                provider.watch_contracts = {(p.symbol, p.strike, p.option_type, p.expiry)
                                            for p in account.open_positions if p.status != "CLOSED"}
            scan_started = time.time()
            snapshots = _fetch_while_polling()
            scan_secs = time.time() - scan_started
            state["scan_secs"].append(scan_secs)
            print(f"[monitor] scan took {scan_secs:.0f}s ({len(snapshots)} symbols)")
            if len(state["scan_secs"]) == 1 and snapshots and config.TELEGRAM_ENABLED:
                send_telegram_message(f"✅ First scan done in {scan_secs / 60:.1f} min ({len(snapshots)} symbols). Scanning continuously now.")
            if not snapshots:
                feed_down_cycles += 1
                print(f"[monitor] {now:%H:%M} - NO data this cycle ({feed_down_cycles} in a row).")
                if feed_down_cycles >= 2 and not feed_alert_sent and config.TELEGRAM_ENABLED:
                    feed_alert_sent = True
                    send_telegram_message(
                        "⚠️ <b>Data feed is down</b> - no market data for "
                        f"{feed_down_cycles} scans in a row. No signals until it recovers."
                    )
                cycles += 1
                if max_cycles and cycles >= max_cycles:
                    break
                _sleep_with_status_polling(config.MONITOR_INTERVAL_SECONDS)
                continue
            if feed_alert_sent and config.TELEGRAM_ENABLED:
                send_telegram_message("✅ Data feed recovered, scanning normally.")
            feed_down_cycles, feed_alert_sent = 0, False
            state["scans"] += 1
            state["symbols_scanned"] = len(snapshots)
            snap_by_symbol = {s.symbol: s for s in snapshots}
            state["last_snap_by_symbol"] = snap_by_symbol
            # Rank ALL qualifying symbols (not just the top N): suppressed / already-alerted names must
            # not use up the per-scan slots, or new symbols could never surface. The per-scan cap
            # (TOP_N_SIGNALS_PER_DAY) is applied below, after filtering.
            ranked = rank_universe(snapshots, top_n=10**6)
            state["last_scan_at"] = now

            # Lighter-weight heads-up alerts for setups building but not yet
            # strong enough to be a full buy signal (see screener.watchlist_candidates).
            # This is about the UNDERLYING STOCK's direction only - no strike
            # selection, no option chain lookup - so it can't fire twice for
            # the same stock just because the ATM strike happened to shift.
            #
            # Two conditions gate it:
            #   1. Momentum: score has to be HIGHER than the last time this
            #      symbol+direction was seen (state["watchlist_score_history"]),
            #      not just sitting anywhere in the band. First appearance
            #      never fires - it only seeds the baseline for next cycle.
            #   2. Once-per-day: a symbol+direction that's already been
            #      watchlist-alerted today (state["watchlisted_today"]) won't
            #      fire again today even if it keeps climbing - one heads-up
            #      per stock per day, not one per cycle.
            score_history = state["watchlist_score_history"]
            watch_candidates = []
            for r in watchlist_candidates(snapshots):
                direction_key = (r.symbol, r.bias)
                previous_score = score_history.get(direction_key)
                score_history[direction_key] = r.score
                if _active_block_reason(r.symbol, "CE" if r.bias == "BULLISH" else "PE"):
                    continue  # a trade in this direction is already running / just closed - no heads-up
                if previous_score is None or r.score <= previous_score:
                    continue  # flat, weakening, or no baseline yet - not worth alerting
                key = (r.symbol, r.bias, today.isoformat())
                if key in state["watchlisted_today"] or (r.symbol, r.bias) in state["signaled_directions_today"]:
                    continue  # already watchlist-alerted (or fully signaled) today
                watch_candidates.append((key, r))
            # watchlist_candidates() is already sorted highest-score-first, so
            # the cap below (secondary safety net) keeps the strongest few on
            # the rare cycle where many symbols strengthen simultaneously.

            new_watch_results = []
            for key, r in watch_candidates[:config.WATCHLIST_MAX_PER_SCAN]:
                state["watchlisted_today"].add(key)
                new_watch_results.append(r)

            if new_watch_results:
                skipped = len(watch_candidates) - len(new_watch_results)
                print(f"[monitor] {now:%H:%M} - {len(new_watch_results)} watchlist candidate(s) "
                      f"(below the buy threshold)"
                      f"{f', {skipped} more capped this cycle' if skipped > 0 else ''}:")
                watch_legs = {}
                for r in new_watch_results:
                    print(f"  \U0001F440 {r.symbol:12s} {r.bias:8s} score {r.score}")
                    snap_w = snap_by_symbol.get(r.symbol)
                    try:
                        watch_legs[r.symbol] = (_pick_strike(snap_w, "CE" if r.bias == "BULLISH" else "PE")
                                                if snap_w else None)
                    except Exception:
                        watch_legs[r.symbol] = None
                    leg_w = watch_legs[r.symbol]
                    state["day_log"]["watchlist"].append({
                        "time": now.strftime("%H:%M"), "symbol": r.symbol,
                        "option_type": "CE" if r.bias == "BULLISH" else "PE", "score": r.score,
                        "contract": (f"{r.symbol} {fmt_strike(leg_w.strike)} {leg_w.option_type}" if leg_w else None),
                    })
                if config.TELEGRAM_ENABLED:
                    for r in new_watch_results:
                        send_telegram_message(format_watchlist_message(r, watch_legs.get(r.symbol)))

            new_ideas = []
            for r in ranked:
                if len(new_ideas) >= config.TOP_N_SIGNALS_PER_DAY:
                    break
                option_type = "CE" if r.bias == "BULLISH" else "PE"
                blocked = _active_block_reason(r.symbol, option_type)
                if blocked:
                    tag = (r.symbol, option_type, blocked, today.isoformat())
                    if tag not in state["suppressed_logged"]:
                        state["suppressed_logged"].add(tag)
                        why = ("a trade is still open" if blocked == "open"
                               else f"cooling down ({config.REENTRY_COOLDOWN_MIN} min after exit)")
                        print(f"[monitor] {now:%H:%M} - no new {r.symbol} {option_type} alert: {why}.")
                    continue
                idea = build_trade_idea(snap_by_symbol[r.symbol], r)
                if not idea:
                    continue
                key = (idea.symbol, idea.option_type, idea.strike, idea.expiry, today.isoformat())
                if key in state["alerted_today"]:
                    continue
                state["alerted_today"].add(key)
                state["signaled_directions_today"].add((idea.symbol, idea.bias))
                new_ideas.append(idea)
            state["last_signal_count"] = len(new_ideas)

            if new_ideas:
                print(f"\n[monitor] {now:%H:%M} - {len(new_ideas)} NEW signal(s):")
                for idea in new_ideas:
                    print(
                        f"  {idea.symbol:12s} {idea.option_type} {fmt_strike(idea.strike):>8s} "
                        f"entry {idea.entry:>7.2f} T1 {idea.target1:>7.2f} T2 {idea.target2:>7.2f} "
                        f"T3 {idea.target3:>7.2f} sl {idea.stoploss:>7.2f} | score {idea.score}"
                    )
                    state["day_log"]["signals"].append({
                        "time": now.strftime("%H:%M"), "symbol": idea.symbol,
                        "option_type": idea.option_type, "strike": idea.strike,
                        "entry": idea.entry, "score": idea.score,
                        "win_probability": idea.win_probability,
                    })
                sent = notify_new_signals(new_ideas)
                if config.TELEGRAM_ENABLED:
                    print(f"[telegram] sent {sent}/{len(new_ideas)} alert(s).")

                if account:
                    open_count = len([p for p in account.open_positions if p.status != "CLOSED"])
                    room = max(config.MAX_TRADES_PER_DAY - open_count, 0)
                    to_open = new_ideas if config.PAPER_TRACK_ALL_SIGNALS else new_ideas[:room]
                    for idea in to_open:
                        pos = account.open_trade(idea)
                        if pos is None:
                            continue
                        print(f"[paper] opened {pos.lots_total} lot(s) of {pos.symbol} {pos.option_type} {pos.strike}")
            else:
                print(f"[monitor] {now:%H:%M} - no new signals this cycle "
                      f"({len(state['alerted_today'])} already alerted today).")

            if account:
                for pos in account.open_positions:
                    if pos.status == "CLOSED":
                        continue
                    snap = snap_by_symbol.get(pos.symbol)
                    if not snap:
                        continue
                    leg = find_leg(snap, pos)
                    if leg:
                        account.mark_to_market(pos.symbol, pos.option_type, pos.strike, leg.ltp)
                print(f"[paper] running summary: {account.summary()}")

                # End-of-day check-in: give a heads-up with current P&L before
                # the automatic square-off, so you can choose to "hold SYMBOL"
                # instead if you want to carry it overnight (see _handle_hold).
                hhmm_now = now.strftime("%H:%M")
                if eod_warning_time <= hhmm_now < config.FORCE_EXIT_TIME:
                    for pos in account.open_positions:
                        if pos.status == "CLOSED" or pos.held_over:
                            continue
                        key = (pos.symbol, pos.option_type, pos.strike, pos.expiry, today.isoformat())
                        if key in state["eod_warned_today"]:
                            continue
                        snap = snap_by_symbol.get(pos.symbol)
                        leg = find_leg(snap, pos)
                        if not leg:
                            continue
                        state["eod_warned_today"].add(key)
                        if config.TELEGRAM_ENABLED:
                            send_telegram_message(format_eod_check_message(pos, leg.ltp))

            cycles += 1
            if max_cycles and cycles >= max_cycles:
                break
            _sleep_with_status_polling(config.MONITOR_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        print("\n[monitor] Stopped by user.")

    if account:
        print("[monitor] Final paper trading summary:", account.summary())


def run_backtest(provider):
    snapshots = fetch_all_snapshots(provider)
    print(f"\n=== Backtest (momentum signal, {config.TRADE_STYLE}) ===")
    print("NOTE: approximated option premium via fixed delta -- see backtest.py docstring.\n")
    for snap in snapshots:
        result = bt.backtest_momentum_signal(snap.history)
        if result.total_signals == 0:
            continue
        print(
            f"{snap.symbol:12s} signals={result.total_signals:>3} "
            f"win_rate={result.win_rate_pct:>5.1f}% "
            f"avg_return={result.avg_return_pct:>6.2f}% per trade"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["watchlist", "paper", "backtest", "monitor"], default="watchlist")
    parser.add_argument("--mock", action="store_true", help="force synthetic data, skip live feeds")
    parser.add_argument("--broker", choices=["nse", "angelone"], default="nse",
                         help="live data source when not using --mock")
    parser.add_argument("--paper", action="store_true",
                         help="with --mode monitor, also track paper positions continuously")
    parser.add_argument("--strict", action="store_true",
                         help="never fall back to mock data; fail loudly (used for unattended runs)")
    parser.add_argument("--skip-if-closed", action="store_true",
                         help="monitor mode: confirm via live data that NSE traded today; if not (holiday), message and exit")
    parser.add_argument("--session-end", default=None, metavar="HH:MM",
                         help="monitor mode: end the run at this IST time (closes paper positions, sends summary)")
    parser.add_argument("--max-cycles", type=int, default=None,
                         help="testing only: stop monitor after N cycles instead of running to market close")
    args = parser.parse_args()

    try:
        provider = get_provider(prefer_live=not args.mock, broker=args.broker, strict=args.strict)
    except Exception as exc:
        send_telegram_message(f"❌ <b>Bot could not start</b>: {exc}")
        raise

    if args.mode == "watchlist":
        run_watchlist(provider)
    elif args.mode == "paper":
        run_paper(provider)
    elif args.mode == "backtest":
        run_backtest(provider)
    elif args.mode == "monitor":
        run_monitor(provider, use_paper=args.paper, max_cycles=args.max_cycles,
                    session_end=args.session_end, skip_if_closed=args.skip_if_closed)
