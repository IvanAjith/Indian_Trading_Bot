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
from screener import rank_universe, watchlist_candidates, needs_option_chain
from strategy import pick_reasoned_strike, build_trade_idea, _pick_strike, max_cost_per_lot
from paper_trader import PaperTradingAccount
from telegram_notifier import (
    notify_new_signals, send_telegram_message, format_exit_message, poll_status_requests,
    format_watchlist_message, format_eod_check_message, format_t1_message, format_trades_summary, format_plan_check, format_trade_ledger, fmt_strike, money as money_s,
)
import backtest as bt


def _minus_minutes(hhmm: str, minutes: int) -> str:
    t = dt.datetime.strptime(hhmm, "%H:%M") - dt.timedelta(minutes=minutes)
    return t.strftime("%H:%M")


CONSECUTIVE_FAILURE_LIMIT = 5  # abort the run instead of grinding through
                               # the whole universe once the feed is clearly down


def _default_universe():
    return [(s, True) for s in config.INDEX_UNIVERSE] + [(s, False) for s in config.STOCK_UNIVERSE]


def fetch_all_snapshots(provider, chain_filter=None):
    universe = provider.get_universe() if hasattr(provider, "get_universe") else _default_universe()
    # A stock/index with an open trade must ALWAYS be scanned, even if it dropped out of today's ranking.
    scanned = {s for s, _ in universe}
    for sym in sorted({c[0] for c in getattr(provider, "watch_contracts", ())} - scanned):
        universe = universe + [(sym, sym in config.INDEX_UNIVERSE)]

    # Fast path: providers that can fetch many symbols per API call (Angel One).
    if hasattr(provider, "get_snapshots"):
        try:
            if chain_filter is not None:
                return provider.get_snapshots(universe, chain_filter=chain_filter)
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
                f"T1 {idea.target1:>7.2f} T2 {idea.target2:>7.2f}"
                f"{f' T3 {idea.target3:>7.2f}' if idea.n_targets >= 3 else ''} "
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


def job_limit_end(session_end, started_at, limit_minutes=None):
    """GitHub stops a hosted job 6 hours after it starts. If the session would not fit (started_at + limit < session_end),
    return the earlier end time ("HH:MM") the session must use; otherwise return session_end unchanged.
    Returns (effective_end, clipped_to) where clipped_to is None when nothing changed."""
    limit_minutes = config.JOB_LIMIT_MINUTES if limit_minutes is None else limit_minutes
    if not session_end:
        return session_end, None
    try:
        h, m = (int(x) for x in session_end.split(":"))
    except ValueError:
        return session_end, None
    end_dt = started_at.replace(hour=h, minute=m, second=0, microsecond=0)
    deadline = started_at + dt.timedelta(minutes=limit_minutes)
    if end_dt <= deadline:
        return session_end, None
    eff = deadline.replace(second=0, microsecond=0).strftime("%H:%M")
    return eff, eff


def run_monitor(provider, use_paper: bool = False, max_cycles: int = None, session_end: str = None,
                skip_if_closed: bool = False, already_alerted: list = None):
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
    session_end, clipped_to = job_limit_end(session_end, dt.datetime.now())
    if clipped_to:
        # square everything off 2 minutes before the forced end, so no trade is left open when GitHub stops the job
        cut = (dt.datetime.strptime(clipped_to, "%H:%M") - dt.timedelta(minutes=2)).strftime("%H:%M")
        config.FORCE_EXIT_TIME = min(config.FORCE_EXIT_TIME, cut)
    state = {
        "last_scan_at": None, "alerted_today": set(), "last_signal_count": 0,
        "day_log": _fresh_day_log(), "watchlisted_today": set(),
        "eod_warned_today": set(), "last_snap_by_symbol": {},
        "scans": 0, "symbols_scanned": 0, "scan_secs": [], "suppressed_logged": set(),
        "alerts_sent_today": 0, "daily_cap_hit": False, "skipped_budget": {}, "shadow_symbols": set(), "shadow_cheaper": set(), "silent_count": 0, "last_scan_done_at": None, "preloaded": False,
        "alerts_by_symbol": {s.strip().upper(): 1 for s in (already_alerted or []) if s.strip()}, "closed_checked": not skip_if_closed,
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

    def _last_scan_text() -> str:
        started, done = state["last_scan_at"], state.get("last_scan_done_at")
        if not done:
            return f"Last scan started {started:%H:%M}."
        mins = max(int((dt.datetime.now() - done).total_seconds() // 60), 0)
        return (f"Last scan started {started:%H:%M}, finished {done:%H:%M} "
                f"({'just now' if mins == 0 else str(mins) + ' min ago'}).")

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
            f"🤖 Bot is running. {_last_scan_text()}\n"
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
        limit_bits = []
        if state["skipped_budget"]:
            names = ", ".join(f"{k} (₹{v:,})" for k, v in sorted(state["skipped_budget"].items(), key=lambda kv: kv[1]))
            lines.append(f"⛔ Skipped as over budget (one lot over ₹{(max_cost_per_lot() or 0):,.0f}, i.e. more than ₹{getattr(config, 'MAX_LOSS_PER_LOT', 0) or 0:,} worst-case loss): "
                         f"{len(state['skipped_budget'])} - {names}")
        if config.MAX_ALERTS_PER_DAY:
            limit_bits.append(f"alerts today: {state['alerts_sent_today']} of {config.MAX_ALERTS_PER_DAY}"
                              f"{' - limit reached' if state['daily_cap_hit'] else ''}")
        if limit_bits:
            lines.append(f"<i>({'; '.join(limit_bits)})</i>")

        if account and account.open_positions:
            lines.append("")
            lines.append(format_trade_ledger(account.open_positions, _live_price))
            lines.append("")
            lines.append(format_plan_check(account.open_positions, _live_price))
            lines.append("\n\U0001F4D3 Trade journal (why each trade was taken, how it played out, lesson): "
                         "journal/ on the journal-data branch.")
        elif fills:
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
        if account and account.shadow_positions:
            n = wins = 0
            pnl = 0.0
            for p in account.shadow_positions:
                price = _live_price(p) if p.status != "CLOSED" else None
                v = p.actual_pnl(price)
                n, wins, pnl = n + 1, wins + (v > 0), pnl + v
            lines.append(f"\n\U0001F507 Silent tracking (below {config.ALERT_SCORE:.0f}, or after {config.NO_ALERTS_AFTER}, "
                         f"no alerts sent): {n} trades, {wins} profit / {n - wins} loss, would-have result {money_s(pnl)} "
                         f"(before charges). Used only for learning.")
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

    def _is_profit(pos) -> bool:
        """A call counts as a PROFIT if it reached Target 1, or (closed at the square-off / manually) ended in the green."""
        if pos.t1_alerted or pos.t1_price > 0 or "T1" in pos.booked_levels:
            return True
        return pos.status == "CLOSED" and pos.actual_pnl() > 0

    def _alerted_positions(symbol: str):
        return [p for p in (account.open_positions if account else []) if p.symbol == symbol and not p.shadow]

    def _profit_strikes(symbol: str) -> set:
        """Strikes of today's profitable calls on this underlying (a repeat call must avoid all of them)."""
        return {p.strike for p in _alerted_positions(symbol) if _is_profit(p)}

    def _is_index_symbol(symbol: str) -> bool:
        return symbol.upper() in {x.upper() for x in config.INDEX_UNIVERSE}

    def _last_close_time(positions):
        """When the most recently closed call on this underlying was closed (None if unknown)."""
        times = []
        for p in positions:
            if p.status == "CLOSED" and p.fills:
                try:
                    times.append(dt.datetime.fromisoformat(p.fills[-1].at))
                except (TypeError, ValueError):
                    pass
        return max(times) if times else None

    def _alert_block_reason(symbol: str, score: float = None, watch: bool = False):
        """Why a NEW alert for this underlying must be suppressed, or None.
        First alert of the day: always allowed. After that (rules agreed 26 Nov 2026):
          - never while the earlier call is still open (wait until it is closed);
          - STOCKS: one alert per stock per day (REALERT_MAX_PER_DAY = 0), whether the call made a profit or a loss;
          - INDEXES: the normal alert score is enough, but REALERT_GAP_MINUTES_INDEX minutes must have passed since the earlier
            call closed, and at most REALERT_MAX_PER_DAY_INDEX alerts per index per day in total.
        The strike rule (a different strike after a profit, the same strike allowed after a loss) is applied once the contract
        is known. Heads-up (watch) messages are never sent once a name has alerted."""
        if not config.ONE_ALERT_PER_UNDERLYING_PER_DAY:
            return None
        mine = _alerted_positions(symbol)
        count = max(state["alerts_by_symbol"].get(symbol, 0), len(mine))
        if count == 0:
            return None
        if watch:
            return "already alerted today"
        if any(p.status != "CLOSED" and p.lots_remaining > 0 for p in mine):
            return "its earlier call is still open"
        if _is_index_symbol(symbol):
            cap = config.REALERT_MAX_PER_DAY_INDEX
            if cap is not None and count >= cap:
                return f"already alerted {count} times today (limit {cap} per index)"
            gap = config.REALERT_GAP_MINUTES_INDEX
            last_close = _last_close_time(mine)
            if gap and last_close is not None and (dt.datetime.now() - last_close).total_seconds() < gap * 60:
                return f"its earlier call closed less than {gap:g} minutes ago"
            if config.REALERT_MIN_SCORE_INDEX is not None and not (score is not None and score > config.REALERT_MIN_SCORE_INDEX):
                return "already alerted today"
            return None
        if score is None or not score > config.REALERT_MIN_SCORE:
            return "already alerted today"
        if config.REALERT_MAX_PER_DAY is not None and count - 1 >= config.REALERT_MAX_PER_DAY:
            return "already alerted today"
        return None

    intraday_cache = {}

    def _intraday_context(symbol: str):
        """15-minute trend of the underlying, for the journal and the alert text only (no effect on scores). Cached for 5 minutes."""
        if provider is None or not hasattr(provider, "get_intraday_context"):
            return None
        hit = intraday_cache.get(symbol)
        if hit and time.time() - hit[0] < 300:
            return hit[1]
        try:
            ctx_i = provider.get_intraday_context(symbol, is_index=_is_index_symbol(symbol))
        except Exception:
            ctx_i = None
        intraday_cache[symbol] = (time.time(), ctx_i)
        return ctx_i

    def _over_budget(entry: float, lot_size: int) -> bool:
        cap = max_cost_per_lot()
        return bool(cap) and entry * lot_size > cap

    def _track_silently(r):
        """A signal below ALERT_SCORE: no alert, no Telegram. Paper-trade it quietly (one per symbol per day, and
        only if the symbol has not alerted) so the journal and weekly review can compare the lower score band."""
        if not account or not config.SHADOW_TRACK_LOWER_BAND or r.score < config.MIN_SIGNAL_SCORE:
            return
        if config.SILENT_TRACK_UNTIL and dt.datetime.now().strftime("%H:%M") >= config.SILENT_TRACK_UNTIL:
            return   # too close to the square-off to teach anything
        if config.SILENT_MAX_PER_DAY and state["silent_count"] >= config.SILENT_MAX_PER_DAY:
            return
        if r.symbol in state["shadow_symbols"] or state["alerts_by_symbol"].get(r.symbol, 0):
            return
        snap_s = snap_by_symbol.get(r.symbol)
        if snap_s is None:
            return
        try:
            idea_s = build_trade_idea(snap_s, r)
        except Exception:
            return
        if not idea_s or _over_budget(idea_s.entry, idea_s.lot_size):
            return
        idea_s.context["entry_clock"] = dt.datetime.now().strftime("%H:%M")
        idea_s.context["intraday"] = _intraday_context(r.symbol)
        nifty_s = snap_by_symbol.get("NIFTY")
        if nifty_s is not None and getattr(nifty_s, "prev_close", 0):
            idea_s.context["nifty_chg_pct"] = round((nifty_s.spot / nifty_s.prev_close - 1) * 100, 2)
        if account.open_trade(idea_s, shadow=True) is not None:
            state["shadow_symbols"].add(r.symbol)
            state["silent_count"] += 1
            print(f"[monitor] silent tracking: {r.symbol} {idea_s.option_type} (score {r.score:.1f}; "
                  f"{'after the ' + config.NO_ALERTS_AFTER + ' cutoff' if r.score >= config.ALERT_SCORE else 'below ' + format(config.ALERT_SCORE, '.0f')})")

    def _shadow_cheaper(r):
        """An over-budget signal: paper-trade it SILENTLY at the reasoned (cheaper) strike so the journal shows whether
        that strike would have worked. No Telegram message. One per underlying per day."""
        if not account or not config.CHEAPER_STRIKE_SHADOW or r.symbol in state["shadow_cheaper"]:
            return
        if config.SILENT_TRACK_UNTIL and dt.datetime.now().strftime("%H:%M") >= config.SILENT_TRACK_UNTIL:
            return
        snap_c = snap_by_symbol.get(r.symbol)
        if snap_c is None:
            return
        option_type = "CE" if r.bias == "BULLISH" else "PE"
        try:
            pick = pick_reasoned_strike(snap_c, option_type, sides="otm")
            idea_c = build_trade_idea(snap_c, r, leg=pick["leg"], strike_pick=pick) if pick else None
        except Exception:
            return
        if not idea_c:
            return
        idea_c.context["entry_clock"] = dt.datetime.now().strftime("%H:%M")
        idea_c.context["intraday"] = _intraday_context(r.symbol)
        idea_c.context["why_shadow"] = "ATM contract over the per-lot budget"
        if account.open_trade(idea_c, shadow=True) is not None:
            state["shadow_cheaper"].add(r.symbol)
            print(f"[monitor] silent tracking (reasoned strike): {r.symbol} {idea_c.option_type} "
                  f"{fmt_strike(idea_c.strike)} (score {r.score:.1f}) - " + "; ".join(idea_c.context.get("strike_pick", [])))

    def _live_price(pos):
        snap = state["last_snap_by_symbol"].get(pos.symbol)
        leg = find_leg(snap, pos)
        return leg.ltp if leg else None

    def _handle_seen(arg: str) -> str:
        """/seen SYMBOL [SYMBOL ...] - mark stocks/indexes as ALREADY alerted today (e.g. after a restart),
        so the one-alert-per-day rule keeps them quiet."""
        symbols = [s.strip().upper() for s in arg.replace(",", " ").split() if s.strip()]
        if not symbols:
            done = ", ".join(sorted(state["alerts_by_symbol"])) or "none"
            return f"Usage: /seen SYMBOL [SYMBOL ...]   Already marked today: {done}"
        for s in symbols:
            state["alerts_by_symbol"][s] = max(state["alerts_by_symbol"].get(s, 0), 1)
        return f"✅ Marked as already alerted today: {', '.join(symbols)}. No more alerts on them today."

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
            f"SL ₹{pos.stoploss:.2f} | T1 ₹{pos.target1:.2f} T2 ₹{pos.target2:.2f}"
            f"{f' T3 ₹{pos.target3:.2f}' if pos.n_targets >= 3 else ''}\n"
            f"I'll alert you on T1 / exits. " + (
                f"Another {symbol} alert only after this trade is closed, {config.REALERT_GAP_MINUTES_INDEX:g} minutes later "
                f"(max {config.REALERT_MAX_PER_DAY_INDEX} a day)." if _is_index_symbol(symbol)
                else f"No more {symbol} alerts today.")
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
            "/seen SYMBOL [SYMBOL ...] - mark stocks as already alerted today\n"
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
        # When a lot is sold at T1, the exit message ("Target 1 hit ...") already carries the instructions.
        if config.TELEGRAM_ENABLED and pos.lots_by_level.get("T1", 0) <= 0:
            send_telegram_message(format_t1_message(pos, price))

    account = PaperTradingAccount(on_fill=_on_fill, on_t1=_on_t1) if use_paper else None
    today = dt.date.today()
    cycles = 0
    telegram_offset = None
    telegram_commands = {
        "status": _status_text, "summary": _summary_text,
        "hold": _handle_hold, "close": _handle_close,
        "help": _handle_help, "start": _handle_help, "add": _handle_add, "seen": _handle_seen,
        "trades": _handle_trades, "pnl": _handle_pnl,
    }

    quick = {"at": 0.0}

    def _quick_mark():
        """Re-price the open (and silently tracked) trades with one small quote call and book any
        target / stoploss / time exit at once, instead of waiting for the next full scan to finish."""
        if account is None or provider is None or not hasattr(provider, "quick_prices"):
            return
        if time.time() - quick["at"] < config.QUICK_CHECK_SECONDS:
            return
        quick["at"] = time.time()
        live = [p for p in account.open_positions + account.shadow_positions
                if p.status != "CLOSED" and p.lots_remaining > 0]
        if not live:
            return
        try:
            prices = provider.quick_prices({(p.symbol, p.strike, p.option_type, p.expiry) for p in live})
            for p in live:
                ltp = prices.get((p.symbol, p.strike, p.option_type, p.expiry))
                if ltp:
                    account.mark_to_market(p.symbol, p.option_type, p.strike, ltp)
        except Exception as exc:   # never let the fast check disturb the session
            print(f"[quick] price check failed (not fatal): {exc}")

    def _sleep_with_status_polling(total_seconds: float):
        nonlocal telegram_offset
        elapsed = 0.0
        step = config.STATUS_POLL_INTERVAL_SECONDS
        while elapsed < total_seconds:
            this_step = min(step, total_seconds - elapsed)
            time.sleep(this_step)
            elapsed += this_step
            _quick_mark()
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
            f"Alerts for scores {config.ALERT_SCORE:.0f}+.\n"
            f"The daily history is loaded first (before the open, if I started early), then the first scan; "
            f"you can already send /add or /trades meanwhile."
            + (f"\n⚠️ GitHub stops a job after 6 hours, so this session can only run until {clipped_to}: "
               f"open trades are squared off at {config.FORCE_EXIT_TIME} and the summary follows. "
               f"(Starting at about 09:15 avoids this.)" if clipped_to else "")
        )

    def _fetch_while_polling(chain_filter=None):
        """Run one market scan in a background thread and keep answering Telegram commands every
        few seconds while it runs. The first scan of a session downloads history for every symbol
        and can take several minutes - commands (/add, /trades, ...) must not sit unanswered."""
        nonlocal telegram_offset
        box = {}

        def work():
            try:
                box["snaps"] = fetch_all_snapshots(provider, chain_filter=chain_filter)
            except Exception as exc:   # surfaced in the main thread below
                box["error"] = exc

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        while worker.is_alive():
            worker.join(timeout=config.STATUS_POLL_INTERVAL_SECONDS)
            if session_end and worker.is_alive() and dt.datetime.now().strftime("%H:%M") >= session_end:
                return None   # session over while a scan is still running: stop waiting, end the session now
            if worker.is_alive():
                _quick_mark()
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
                state["alerts_by_symbol"] = {}
                state["alerts_sent_today"], state["shadow_symbols"] = 0, set()
                state["shadow_cheaper"] = set()
                state["silent_count"] = 0
                state["daily_cap_hit"], state["skipped_budget"] = False, {}
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
                if (before_open and config.PRELOAD_BEFORE_OPEN and not state["preloaded"]
                        and hasattr(provider, "preload") and not state["closed_checked"]):
                    state["preloaded"] = True       # use the wait: download the daily history now, not during the first scan
                    try:
                        print("[monitor] pre-loading daily history while waiting for the open...")
                        provider.preload()
                    except Exception as exc:
                        print(f"[monitor] pre-load failed (not fatal): {exc}")
                    continue
                reason = f"opens at {config.MARKET_OPEN_TIME}" if before_open else "closed for the day"
                print(f"[monitor] {now:%H:%M} - market {reason}. Idling (still answering Telegram status/summary)...")
                _sleep_with_status_polling(min(config.MONITOR_INTERVAL_SECONDS, 60))
                cycles += 1
                if max_cycles and cycles >= max_cycles:
                    break
                continue

            if not state["closed_checked"]:
                if now.strftime("%H:%M") < "09:20":
                    if config.PRELOAD_BEFORE_OPEN and not state["preloaded"] and hasattr(provider, "preload"):
                        state["preloaded"] = True
                        try:
                            print("[monitor] pre-loading daily history while waiting for the open...")
                            provider.preload()
                        except Exception as exc:
                            print(f"[monitor] pre-load failed (not fatal): {exc}")
                        continue
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
                                            for p in account.open_positions + account.shadow_positions
                                            if p.status != "CLOSED"}
            scan_started = time.time()
            two_stage = (config.TWO_STAGE_SCAN and state["scans"] % max(config.TWO_STAGE_FULL_EVERY, 1) != 0
                         and hasattr(provider, "get_snapshots"))
            snapshots = _fetch_while_polling(chain_filter=needs_option_chain if two_stage else None)
            if snapshots is None:
                _end_session()
                break
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
            state["last_scan_done_at"] = dt.datetime.now()
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
            cand_legs = {}
            for r in watchlist_candidates(snapshots):
                direction_key = (r.symbol, r.bias)
                previous_score = score_history.get(direction_key)
                score_history[direction_key] = r.score
                if _alert_block_reason(r.symbol, watch=True):
                    continue  # this stock/index already alerted today - no heads-up
                if previous_score is None or r.score <= previous_score:
                    continue  # flat, weakening, or no baseline yet - not worth alerting
                if state["daily_cap_hit"] or (config.NO_ALERTS_AFTER
                                              and now.strftime("%H:%M") >= config.NO_ALERTS_AFTER):
                    continue  # no more entry alerts today (limit used up / past the cutoff) - a heads-up would lead nowhere
                snap_c = snap_by_symbol.get(r.symbol)
                try:
                    leg_c = _pick_strike(snap_c, "CE" if r.bias == "BULLISH" else "PE") if snap_c else None
                except Exception:
                    leg_c = None
                if leg_c is not None and _over_budget(leg_c.ltp, leg_c.lot_size):
                    continue  # contract is over the per-lot budget - not interesting for this account
                cand_legs[r.symbol] = leg_c
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
                    watch_legs[r.symbol] = cand_legs.get(r.symbol)
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
            after_cutoff = bool(config.NO_ALERTS_AFTER) and now.strftime("%H:%M") >= config.NO_ALERTS_AFTER
            for r in ranked:                      # best score first
                if r.score < config.ALERT_SCORE or after_cutoff:
                    _track_silently(r)            # below the alert score, or too late in the day: no alert,
                    continue                      # paper-traded quietly for the journal only
                if len(new_ideas) >= config.MAX_ALERTS_PER_SCAN:
                    continue                      # not lost: it alerts in a later scan if it still qualifies
                if config.MAX_ALERTS_PER_DAY and state["alerts_sent_today"] + len(new_ideas) >= config.MAX_ALERTS_PER_DAY:
                    continue
                option_type = "CE" if r.bias == "BULLISH" else "PE"
                blocked = _alert_block_reason(r.symbol, r.score)
                if blocked:
                    tag = (r.symbol, blocked, today.isoformat())
                    if tag not in state["suppressed_logged"]:
                        state["suppressed_logged"].add(tag)
                        print(f"[monitor] {now:%H:%M} - no new {r.symbol} alert: {blocked} "
                              f"(stocks: one alert per day; indexes: earlier call closed, {config.REALERT_GAP_MINUTES_INDEX:g} min since the close, "
                              f"max {config.REALERT_MAX_PER_DAY_INDEX} a day).")
                    continue
                snap_r = snap_by_symbol[r.symbol]
                idea = build_trade_idea(snap_r, r)
                if not idea:
                    continue
                used_strikes = set()
                if state["alerts_by_symbol"].get(r.symbol, 0) or _alerted_positions(r.symbol):
                    used_strikes = _profit_strikes(r.symbol)   # a repeat call after a PROFIT must use a different strike
                if idea.strike in used_strikes:
                    pick = pick_reasoned_strike(snap_r, option_type, exclude_strikes=used_strikes, sides="any")
                    if pick is None:
                        tag = (r.symbol, "no different strike", today.isoformat())
                        if tag not in state["suppressed_logged"]:
                            state["suppressed_logged"].add(tag)
                            print(f"[monitor] {now:%H:%M} - no new {r.symbol} alert: its earlier call made a profit at "
                                  f"{fmt_strike(idea.strike)} and no other strike passes the checks.")
                        _track_silently(r)
                        continue
                    idea = build_trade_idea(snap_r, r, leg=pick["leg"], strike_pick=pick)
                    if not idea:
                        continue
                if _over_budget(idea.entry, idea.lot_size):
                    # ATM costs more than the budget: look along the chain for a cheaper strike chosen by reasons (an alert),
                    # otherwise skip it (counted in the daily summary) and test the reasoned strike silently
                    atm_cost = round(idea.entry * idea.lot_size)
                    pick = None
                    if config.CHEAPER_STRIKE_ALERTS:
                        pick = pick_reasoned_strike(snap_r, option_type, exclude_strikes=used_strikes, sides="otm")
                    if pick is not None:
                        idea = build_trade_idea(snap_r, r, leg=pick["leg"], strike_pick=pick)
                        if idea:
                            idea.context["atm_cost_per_lot"] = atm_cost
                    if pick is None or not idea:
                        state["skipped_budget"][r.symbol] = atm_cost
                        _shadow_cheaper(r)
                        continue
                key = (idea.symbol, idea.option_type, idea.strike, idea.expiry, today.isoformat())
                is_repeat = bool(state["alerts_by_symbol"].get(idea.symbol, 0) or _alerted_positions(idea.symbol))
                if key in state["alerted_today"] and not is_repeat:
                    continue   # (a repeat call on the same contract after a LOSS is allowed - see _alert_block_reason)
                idea.context["entry_clock"] = now.strftime("%H:%M")
                idea.context["intraday"] = _intraday_context(idea.symbol)
                nifty = snap_by_symbol.get("NIFTY")
                if nifty is not None and getattr(nifty, "prev_close", 0):
                    idea.context["nifty_chg_pct"] = round((nifty.spot / nifty.prev_close - 1) * 100, 2)
                state["alerted_today"].add(key)
                state["alerts_by_symbol"][idea.symbol] = state["alerts_by_symbol"].get(idea.symbol, 0) + 1
                state["signaled_directions_today"].add((idea.symbol, idea.bias))
                new_ideas.append(idea)
            state["last_signal_count"] = len(new_ideas)
            state["alerts_sent_today"] += len(new_ideas)
            if (config.MAX_ALERTS_PER_DAY and not state["daily_cap_hit"]
                    and state["alerts_sent_today"] >= config.MAX_ALERTS_PER_DAY):
                state["daily_cap_hit"] = True
                print(f"[monitor] {now:%H:%M} - daily limit of {config.MAX_ALERTS_PER_DAY} alerts reached.")
                if config.TELEGRAM_ENABLED and new_ideas:
                    state["announce_cap"] = True

            if new_ideas:
                print(f"\n[monitor] {now:%H:%M} - {len(new_ideas)} NEW signal(s):")
                for idea in new_ideas:
                    print(
                        f"  {idea.symbol:12s} {idea.option_type} {fmt_strike(idea.strike):>8s} "
                        f"entry {idea.entry:>7.2f} T1 {idea.target1:>7.2f} T2 {idea.target2:>7.2f} "
                        f"{f'T3 {idea.target3:>7.2f} ' if idea.n_targets >= 3 else ''}sl {idea.stoploss:>7.2f} | score {idea.score}"
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
                if state.pop("announce_cap", False):
                    send_telegram_message(
                        f"\U0001F512 Daily limit of {config.MAX_ALERTS_PER_DAY} entry alerts reached - no more "
                        f"entry alerts today. I keep tracking your open trades.")

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
                for pos in account.open_positions + account.shadow_positions:
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
    parser.add_argument("--already-alerted", default="", metavar="SYM,SYM",
                         help="monitor mode: stocks/indexes that already alerted today (e.g. after a restart) - they stay quiet")
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
                    session_end=args.session_end, skip_if_closed=args.skip_if_closed,
                    already_alerted=args.already_alerted.split(","))
