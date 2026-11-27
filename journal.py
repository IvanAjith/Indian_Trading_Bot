"""
journal.py
----------
The trade journal. When a paper trade is fully closed, one entry is written:

  * WHY it was taken      - score, the technical triggers, volume, open interest, market trend
  * HOW it played out     - timeline, highest/lowest price, which targets were hit, how it ended
  * WHY it worked / failed - rule-based diagnosis from the measured facts (NOT a guess about the
                            future: "likely contributors", each tied to a number)
  * LESSON and IMPROVEMENT - what to look at next time

Two files are written under config.JOURNAL_DIR:
  YYYY-MM-DD.md   human-readable, one section per trade
  trades.jsonl    one JSON object per trade (facts + diagnosis) - read by weekly_review.py

The diagnosis is deliberately simple and transparent. The weekly review (weekly_review.py) then looks
across many trades to see which of these factors really go with winning or losing.
"""

import datetime as dt
import json
import os

import config

# ---- thresholds used by the diagnosis (kept here so they are easy to read and change) ----
LOW_VOLUME = 2.0          # volume vs 20-day average below this = weak participation
STRONG_VOLUME = 3.0
NEAR_THRESHOLD_MARGIN = 3  # a score within this many points of MIN_SIGNAL_SCORE is "barely qualified"
CHASE_PCT = 2.0           # underlying already moved this much (in the trade direction) today
AGAINST_MARKET_PCT = 0.3  # NIFTY moving this much against the trade
OPENING_UNTIL = "09:50"   # first half hour after the open
LATE_FROM = "14:00"
QUICK_STOP_MIN = 10       # a stop hit this fast after entry looks like noise / a spike entry
NEAR_MISS_PCT = 10.0      # stopped out after getting this close to T1 (+15%)
RSI_STRETCHED_HIGH = getattr(config, 'STRETCHED_RSI_HIGH', 70)   # a call bought with RSI at/above this (or a put at/below RSI_STRETCHED_LOW) = trend already stretched
RSI_STRETCHED_LOW = getattr(config, 'STRETCHED_RSI_LOW', 30)

# risk-factor code -> (short reason, what to do about it)
RISK_FACTORS = {
    "low_volume": ("volume was weak", "Require a higher volume ratio before alerting (try 2x or more)."),
    "no_oi": ("no open-interest confirmation", "Prefer signals where OI buildup agrees with the direction."),
    "against_market": ("it went against the NIFTY trend", "Skip signals that oppose the NIFTY trend of the day."),
    "chased": ("the stock had already made a big move today", "Avoid entries after a large same-direction move; wait for a pullback."),
    "opening": ("entered in the volatile first half hour", "Consider waiting until about 09:50 before taking signals."),
    "late_entry": ("entered late in the day with little time left", "Stop new entries after about 14:00."),
    "near_threshold": ("the score only just passed the threshold", "Consider a higher minimum score."),
    "quick_stop": ("the stop was hit within minutes of entry", "Entry may have been at a spike; look at waiting for confirmation."),
    "oi_against": ("open interest built up AGAINST the trade's direction",
                   "Do not count OI build-up that points the other way (a call with PUT build-up, a put with CALL build-up); require OI to agree with the direction."),
    "intraday_against": ("the stock's 15-minute trend was against the trade at entry",
                         "Require the 15-minute trend (EMA9 vs EMA20) to agree with the trade before alerting (the call is based on the daily trend only)."),
    "stretched": ("the trend was already stretched (RSI 70+ for a call, 30- for a put)",
                  "Skip or down-weight calls when RSI is 70+ and puts when RSI is 30 or below - the move may be exhausted."),
}
GOOD_FACTORS = {
    "strong_volume": "strong volume",
    "oi_confirmed": "open interest confirmed the direction",
    "with_market": "it agreed with the NIFTY trend",
    "high_score": "high score",
    "intraday_agrees": "the stock's 15-minute trend agreed with the trade at entry",
    "continuation": "continuation profile (strong same-direction move today + agreeing OI + trend not stretched)",
}


def _parse(ts):
    try:
        return dt.datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def _clock(ts):
    d = _parse(ts)
    return d.strftime("%H:%M") if d else "n/a"


def _money(x):
    return f"{'+' if x > 0 else '-' if x < 0 else ''}₹{abs(x):,.2f}"


# --------------------------------------------------------------------------------------------
def facts(pos) -> dict:
    """Everything measurable about a closed trade."""
    a_cap, a_ret, a_pnl = pos.plan_a()
    b_cap, b_ret, b_pnl = pos.plan_b()
    opened, closed = _parse(pos.opened_at), _parse(pos.fills[-1].at) if pos.fills else None
    minutes_open = round((closed - opened).total_seconds() / 60, 1) if opened and closed else None
    t1 = _parse(pos.t1_at)
    levels = list(dict.fromkeys(f.level for f in pos.fills))
    ctx = pos.context or {}
    entry = pos.entry
    return {
        "date": (opened or dt.datetime.now()).date().isoformat(),
        "symbol": pos.symbol, "option_type": pos.option_type, "strike": pos.strike, "expiry": pos.expiry,
        "is_index": bool(ctx.get("is_index")), "manual": bool(ctx.get("manual")),
        "alerted": not getattr(pos, "shadow", False),
        "variant": ctx.get("variant"), "strike_pick": ctx.get("strike_pick"),
        "score_parts": ctx.get("score_parts"), "rules_version": ctx.get("rules_version"),
        "intraday": ctx.get("intraday"), "trend_daily": ctx.get("trend_daily"),
        "path": list(getattr(pos, "path", []) or []),
        "lots": pos.lots_total, "lot_size": pos.lot_size, "n_targets": pos.n_targets,
        "entry": entry, "cost_per_lot": round(entry * pos.lot_size, 2),
        "stoploss": None, "target1": pos.target1, "target2": pos.target2, "target3": pos.target3,
        "score": pos.score, "reasons": list(pos.reasons or []),
        "rsi": ctx.get("rsi"), "volume_ratio": ctx.get("volume_ratio"), "oi_signal": ctx.get("oi_signal"),
        "spot_chg_pct": ctx.get("spot_chg_pct"), "nifty_chg_pct": ctx.get("nifty_chg_pct"),
        "entry_clock": ctx.get("entry_clock") or _clock(pos.opened_at),
        "exit_clock": _clock(pos.fills[-1].at) if pos.fills else "n/a",
        "minutes_open": minutes_open,
        "t1_reached": pos.t1_price > 0, "t1_clock": _clock(pos.t1_at) if t1 else None,
        "minutes_to_t1": round((t1 - opened).total_seconds() / 60, 1) if t1 and opened else None,
        "levels_hit": levels,
        "exit_level": levels[-1] if levels else None,
        "stop_at_exit": pos.stoploss,
        "peak_price": pos.peak_price, "trough_price": pos.trough_price,
        "peak_gain_pct": round((pos.peak_price / entry - 1) * 100, 1) if entry and pos.peak_price else None,
        "worst_dip_pct": round((pos.trough_price / entry - 1) * 100, 1) if entry and pos.trough_price else None,
        "a_capital": a_cap, "a_returned": a_ret, "a_pnl": a_pnl,
        "b_capital": b_cap, "b_returned": b_ret, "b_pnl": b_pnl,
    }


def diagnose(f: dict, pos=None) -> dict:
    """Rule-based reading of the facts: outcome, what lined up, likely contributors, lesson, improvements."""
    win = f["a_pnl"] > 0
    last = f"T{f['n_targets']}"
    levels = f["levels_hit"]
    stop = f.get("stop_at_exit")
    entry = f["entry"]

    # ---- how it ended
    if last in levels:
        outcome = "ALL_TARGETS"
        label = "all targets hit"
    elif f["exit_level"] == "SL":
        if f["t1_reached"] and stop is not None and f["target1"] and stop >= f["target1"]:
            outcome, label = "TRAIL_STOP", "stopped by the trailing stop at T1 after T2"
        elif f["t1_reached"]:
            outcome, label = "COST_STOP", "back to cost after T1 (cost-to-cost stop)"
        else:
            outcome, label = "STOP_BEFORE_T1", "stoploss hit before any target"
    elif f["exit_level"] == "TIME":
        outcome, label = "SQUARE_OFF", "closed at the square-off time"
    elif f["exit_level"] == "MANUAL":
        outcome, label = "MANUAL", "closed manually"
    else:
        outcome, label = "OTHER", f"closed at {f['exit_level']}"

    # ---- risk factors (things that went against the trade) and good factors (things that lined up)
    risks, goods = [], []
    vol = f.get("volume_ratio")
    if vol is not None:
        if vol < LOW_VOLUME:
            risks.append(("low_volume", f"volume only {vol:.1f}x the 20-day average"))
        elif vol >= STRONG_VOLUME:
            goods.append(("strong_volume", f"volume {vol:.1f}x the 20-day average"))
    oi = f.get("oi_signal")
    want_up = f["option_type"] == "CE"
    oi_aligned = (want_up and oi == "long_buildup") or (not want_up and oi == "short_buildup")
    oi_against = (want_up and oi == "short_buildup") or (not want_up and oi == "long_buildup")
    if oi_aligned:
        goods.append(("oi_confirmed", f"options OI shows {oi.replace('_', ' ')}, agreeing with the {'call' if want_up else 'put'}"))
    elif oi_against and not f["manual"]:
        risks.append(("oi_against", f"options OI shows {oi.replace('_', ' ')}, which points AGAINST a {'call' if want_up else 'put'}"))
    elif oi is not None and not f["manual"]:
        risks.append(("no_oi", f"open interest was {str(oi).replace('_', ' ')}, not confirming"))
    intr = f.get("intraday") or {}
    if intr.get("ema_up_15m") is not None and not f["manual"]:
        if bool(intr["ema_up_15m"]) == want_up:
            goods.append(("intraday_agrees", f"the 15-minute trend agreed (EMA9 {'above' if intr['ema_up_15m'] else 'below'} EMA20)"))
        else:
            risks.append(("intraday_against", f"the 15-minute trend was against the trade (EMA9 {'above' if intr['ema_up_15m'] else 'below'} EMA20)"))
    rsi = f.get("rsi")
    stretched = rsi is not None and ((want_up and rsi >= RSI_STRETCHED_HIGH) or (not want_up and rsi <= RSI_STRETCHED_LOW))
    if stretched and not f["manual"]:
        risks.append(("stretched", f"RSI {rsi:.0f}: the trend was already stretched for a {'call' if want_up else 'put'}"))
    nifty = f.get("nifty_chg_pct")
    if nifty is not None and not f["is_index"]:
        want_up = f["option_type"] == "CE"
        if (want_up and nifty <= -AGAINST_MARKET_PCT) or (not want_up and nifty >= AGAINST_MARKET_PCT):
            risks.append(("against_market", f"NIFTY was {nifty:+.1f}% against a {'bullish' if want_up else 'bearish'} trade"))
        elif (want_up and nifty >= 0.1) or (not want_up and nifty <= -0.1):
            goods.append(("with_market", f"NIFTY {nifty:+.1f}% agreed with the trade"))
    chg = f.get("spot_chg_pct")
    if chg is not None:
        moved = chg if f["option_type"] == "CE" else -chg
        if moved >= CHASE_PCT:
            risks.append(("chased", f"the underlying had already moved {chg:+.1f}% today"))
            if oi_aligned and not stretched:
                goods.append(("continuation", f"strong {chg:+.1f}% move today with agreeing OI and RSI {rsi:.0f} not stretched "
                                              f"(a continuation profile - the review tests this against 'chased')"))
    clock = f.get("entry_clock") or ""
    if clock and clock < OPENING_UNTIL:
        risks.append(("opening", f"entered at {clock}, in the volatile first half hour"))
    if clock and clock >= LATE_FROM:
        risks.append(("late_entry", f"entered at {clock}, late in the day"))
    thr = getattr(config, "ALERT_SCORE", 75)
    if f.get("score") is not None and f["score"] < thr + NEAR_THRESHOLD_MARGIN and not f["manual"]:
        risks.append(("near_threshold", f"score {f['score']:.0f}, close to or below the {thr:.0f} alert threshold"))
    if f.get("score") is not None and f["score"] >= getattr(config, "ALERT_SCORE", 75) + 5:
        goods.append(("high_score", f"strong score {f['score']:.0f}"))
    if outcome == "STOP_BEFORE_T1" and f.get("minutes_open") is not None and f["minutes_open"] <= QUICK_STOP_MIN:
        risks.append(("quick_stop", f"stoploss hit {f['minutes_open']:.0f} min after entry"))

    # ---- how it played out
    played = [f"Entered at ₹{entry:.2f} at {f['entry_clock']} ({f['lots']} lot × {f['lot_size']}, "
              f"cost ₹{f['a_capital']:,.0f})."]
    if f["t1_reached"]:
        played.append(f"Target 1 reached at {f['t1_clock']}"
                      + (f" ({f['minutes_to_t1']:.0f} min after entry)." if f["minutes_to_t1"] is not None else "."))
    if f.get("peak_gain_pct") is not None:
        played.append(f"Highest price ₹{f['peak_price']:.2f} ({f['peak_gain_pct']:+.1f}% from entry); "
                      f"lowest ₹{f['trough_price']:.2f} ({f['worst_dip_pct']:+.1f}%).")
    played.append(f"Ended: {label} at {f['exit_clock']}"
                  + (f" after {f['minutes_open']:.0f} min." if f["minutes_open"] is not None else "."))
    played.append(f"Result: {_money(f['a_pnl'])} on the full plan; {_money(f['b_pnl'])} if only 1 lot had been sold at T1.")

    # ---- reading of the outcome
    reading = []
    if outcome == "STOP_BEFORE_T1":
        peak = f.get("peak_gain_pct") or 0
        if peak >= NEAR_MISS_PCT:
            reading.append(f"It got within reach of Target 1 (peaked at {peak:+.1f}% against a +15% target) and then reversed.")
        elif peak < 3:
            reading.append("The price never moved in the trade's favour; the signal did not follow through.")
        else:
            reading.append(f"It moved only {peak:+.1f}% in favour before falling to the stoploss.")
    elif outcome == "COST_STOP":
        reading.append(f"Target 1 was hit and profit booked, then the rest fell back to the cost stop (peak {f.get('peak_gain_pct')}%)."
                       + (" The stop did its job: a small result instead of a loss." if win else ""))
    elif outcome == "TRAIL_STOP":
        reading.append("Targets 1 and 2 were booked and the last lot was protected by the trailing stop.")
    elif outcome == "ALL_TARGETS":
        reading.append("The move carried through every target.")
    elif outcome == "SQUARE_OFF":
        reading.append("The trade neither hit its stop nor all targets and was closed at the square-off time.")

    # ---- lesson and improvement
    if win:
        if goods:
            lesson = "What lined up: " + "; ".join(t for _, t in goods) + "."
        else:
            lesson = "Won without any of the usual confirming factors - treat as luck until it repeats."
        improve = ["Keep the rules as they are; check in the weekly review whether these factors really go with wins."]
        if risks:
            improve.append("It won despite: " + "; ".join(t for _, t in risks) + " - note it, don't over-trust it.")
    else:
        if risks:
            lesson = "Likely contributors: " + "; ".join(t for _, t in risks) + "."
            improve = [RISK_FACTORS[c][1] for c, _ in risks]
        else:
            lesson = "No obvious warning sign in the measured factors - this looks like ordinary market noise."
            improve = ["No rule change suggested from this single trade."]

    return {
        "outcome": outcome, "outcome_label": label, "win": win,
        "risk_codes": [c for c, _ in risks], "good_codes": [c for c, _ in goods],
        "risks": [t for _, t in risks], "goods": [t for _, t in goods],
        "played": played, "reading": reading, "lesson": lesson, "improve": improve,
    }


def render_markdown(f: dict, d: dict, why_taken: str) -> str:
    kind = "index" if f["is_index"] else "stock"
    icon = "✅ WIN" if d["win"] else ("❌ LOSS" if f["a_pnl"] < 0 else "➖ FLAT")
    lines = [
        f"### {f['entry_clock']} · {f['symbol']} {f['expiry']} {f['strike']:g} {f['option_type']} "
        f"({kind}, {f['lots']} lots{'' if f.get('alerted', True) else ', SILENT - no alert was sent (below the alert score, or after the cutoff time)'}) "
        f"— {icon} {_money(f['a_pnl'])}",
        f"**Outcome:** {d['outcome_label']}",
        f"**Why it was taken:** {why_taken}",
        "**How it played out:**",
    ]
    lines += [f"- {p}" for p in d["played"]]
    if d["reading"]:
        lines += [f"- {r}" for r in d["reading"]]
    lines.append(f"**Why it {'worked' if d['win'] else 'did not work'}:** "
                 + ("; ".join(d["goods"] if d["win"] else d["risks"]) or "no clear factor in the measured data") + ".")
    lines.append(f"**Lesson:** {d['lesson']}")
    lines.append("**How to improve:**")
    lines += [f"- {i}" for i in d["improve"]]
    lines.append("")
    return "\n".join(lines)


def _why_taken(f: dict) -> str:
    bits = []
    if f["manual"]:
        return "added manually with /add (no bot signal behind it)."
    bits.append(f"score {f['score']:.0f}")
    if f["reasons"]:
        bits.append("; ".join(f["reasons"]))
    if f.get("rsi") is not None:
        bits.append(f"RSI {f['rsi']}")
    if f.get("nifty_chg_pct") is not None and not f["is_index"]:
        bits.append(f"NIFTY {f['nifty_chg_pct']:+.1f}% on the day")
    return " | ".join(bits) + "."


def record_trade(pos, directory: str = None):
    """Called when a paper trade is fully closed. Never raises into the trading loop's caller
    (the caller wraps it) - a journal problem must not stop trading."""
    directory = directory or config.JOURNAL_DIR
    os.makedirs(directory, exist_ok=True)
    f = facts(pos)
    f["stoploss"] = pos.stoploss
    d = diagnose(f, pos)
    row = dict(f)
    row.update({k: d[k] for k in ("outcome", "outcome_label", "win", "risk_codes", "good_codes", "lesson")})
    with open(os.path.join(directory, "trades.jsonl"), "a") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    md_path = os.path.join(directory, f"{f['date']}.md")
    new_file = not os.path.exists(md_path)
    with open(md_path, "a") as fh:
        if new_file:
            fh.write(f"# Trade journal - {f['date']}\n\n"
                     "_Rule-based entries written by the bot. 'Likely contributors' are measured facts that "
                     "tend to go with a loss; they are hints, not proof._\n\n")
        fh.write(render_markdown(f, d, _why_taken(f)) + "\n")
    return row
