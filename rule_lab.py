"""
rule_lab.py
-----------
The nightly learning loop. The LIVE rules stay fixed all week; every night this script replays CANDIDATE rules on the journal
and reports what each would have done, so the weekly meeting can decide from evidence (and from days the candidate had not
seen when it was proposed) instead of from a rule fitted to one day.

  python rule_lab.py --journal-dir journal [--date YYYY-MM-DD] [--telegram]

Writes   <journal-dir>/lab/<date>.md      tonight's report (data checks, today's winners vs losers, candidates tonight)
         <journal-dir>/lab/candidates.md  cumulative table per candidate: in-sample vs out-of-sample, and the trade-off

How a candidate is judged
  * In-sample  = days up to and including the day it was proposed (it was shaped by them: do not trust those numbers).
  * Out-of-sample = later days with clean data. Only these count as evidence.
  * Trade-off per candidate: winners it would have dropped, losers it would have avoided, new alerts it would have added.
  * Fewer than MIN_OOS_ALERTS out-of-sample alerts = "too early to say".
Replay limits (be honest when reading it): only trades that were opened are in the journal (alerts, and silent trades with a
score of 60 or more); the one-alert-per-name rule is approximated (earliest qualifying row per name per day); results are paper
fills with 1% slippage.
"""

import argparse
import collections
import datetime as dt
import os
import sys

import config
import screener
import weekly_review as wr

MIN_OOS_ALERTS = 30
# Days whose volume factor was unreliable (the 4-hour cached daily candle): usable for the in-sample story only.
STALE_VOLUME_DATES = {"2026-11-25", "2026-11-26"}
REQUIRED_FIELDS = ("score", "rsi", "oi_signal", "spot_chg_pct", "entry_clock", "volume_ratio")


# ---- facts about one journal row ---------------------------------------------------------------------------------------
def is_stock(r):
    return not r.get("is_index")


def moved_pct(r):
    chg = r.get("spot_chg_pct")
    if chg is None:
        return 0.0
    return chg if r.get("option_type") == "CE" else -chg


def oi_aligned(r):
    return (r.get("option_type") == "CE" and r.get("oi_signal") == "long_buildup") or \
           (r.get("option_type") == "PE" and r.get("oi_signal") == "short_buildup")


def oi_against(r):
    return (r.get("option_type") == "CE" and r.get("oi_signal") == "short_buildup") or \
           (r.get("option_type") == "PE" and r.get("oi_signal") == "long_buildup")


def stretched(r):
    rsi = r.get("rsi")
    if rsi is None:
        return False
    return (r.get("option_type") == "CE" and rsi >= config.STRETCHED_RSI_HIGH) or \
           (r.get("option_type") == "PE" and rsi <= config.STRETCHED_RSI_LOW)


def live_score(r):
    """The score under the CURRENT live rules. Rows written before the rules changed are converted with the same adjustments."""
    if r.get("rules_version") == config.RULES_VERSION:
        return r.get("score") or 0.0
    s = r.get("score") or 0.0
    if config.OI_DIRECTION_AWARE and oi_against(r):
        s += screener.W_OI * (45.0 - screener.OI_SCORE_MAX)
    st = stretched(r)
    if st and is_stock(r):
        s -= config.STRETCHED_RSI_PENALTY
    if (not st and moved_pct(r) >= config.CONTINUATION_MIN_MOVE_PCT and oi_aligned(r)):
        s += config.CONTINUATION_BONUS
    return s


# ---- candidate rules (each is a delta on the live score and/or a gate) ------------------------------------------------
def _rsi_cap(r, cap=65.0):
    rsi = r.get("rsi")
    if rsi is None:
        return 0.0
    ce = r.get("option_type") == "CE"
    excess = (rsi - cap) if ce else ((100 - cap) - rsi)
    return -screener.W_MOM * 2.0 * excess if excess > 0 else 0.0


def _vol_points(r):
    """The volume part of the score (0-100) implied by the row's volume ratio."""
    v = r.get("volume_ratio")
    if v is None:
        return 40.0
    return min(100.0, max(0.0, (v - 1.0) * 60.0 + 40.0))


CANDIDATES = [
    {"name": "rsi_cap_65", "proposed": "2026-11-26",
     "desc": "momentum earns no extra credit for RSI above 65 (calls) / below 35 (puts)",
     "delta": _rsi_cap, "gate": lambda r: True},
    {"name": "stretched_penalty_10", "proposed": "2026-11-26",
     "desc": "stretched-trend penalty doubled (stocks): -10 instead of -5",
     "delta": lambda r: -config.STRETCHED_RSI_PENALTY if (stretched(r) and is_stock(r)) else 0.0, "gate": lambda r: True},
    {"name": "continuation_bonus_10", "proposed": "2026-11-26",
     "desc": "continuation profile bonus raised to +10 (from +6)",
     "delta": lambda r: 4.0 if (not stretched(r) and moved_pct(r) >= config.CONTINUATION_MIN_MOVE_PCT and oi_aligned(r)) else 0.0,
     "gate": lambda r: True},
    {"name": "oi_must_agree", "proposed": "2026-11-26",
     "desc": "no alert unless open interest agrees with the trade (stocks)",
     "delta": lambda r: 0.0, "gate": lambda r: (not is_stock(r)) or oi_aligned(r)},
    {"name": "move_1pct_with_trade", "proposed": "2026-11-26",
     "desc": "no stock alert unless the stock is already 1%+ in the trade's direction today",
     "delta": lambda r: 0.0, "gate": lambda r: (not is_stock(r)) or moved_pct(r) >= 1.0},
    {"name": "day_move_not_against", "proposed": "2026-11-27",
     "desc": "no stock alert when the stock is already moving AGAINST the trade today by more than 0.5% (a put on a stock that is up, a call on a stock that is down)",
     "delta": lambda r: 0.0, "gate": lambda r: (not is_stock(r)) or moved_pct(r) >= -0.5},
    {"name": "volume_credit_only_if_move_agrees", "proposed": "2026-11-27",
     "desc": "volume only earns its points when today's move agrees with the trade (high volume on a rally is no support for a put)",
     "delta": lambda r: (-screener.W_VOL * (_vol_points(r) - 40.0)) if (_vol_points(r) > 40.0 and moved_pct(r) < 0) else 0.0,
     "gate": lambda r: True},
    {"name": "daily_price_vs_ema9", "proposed": "2026-11-27",
     "desc": "no stock alert unless the stock's price is on the right side of its DAILY EMA9 (above for a call, below for a put); rows without the data pass",
     "delta": lambda r: 0.0,
     "gate": lambda r: (not is_stock(r)) or (r.get("trend_daily") or {}).get("price_vs_ema9_pct") is None or
                       ((r["trend_daily"]["price_vs_ema9_pct"] >= 0) == (r.get("option_type") == "CE"))},
    {"name": "daily_ema_gap_1pct", "proposed": "2026-11-27",
     "desc": "no stock alert unless the DAILY EMA9/EMA20 gap is at least 1% in the trade's direction (a real trend, not a thin one); rows without the data pass",
     "delta": lambda r: 0.0,
     "gate": lambda r: (not is_stock(r)) or (r.get("trend_daily") or {}).get("ema_gap_pct") is None or
                       (((r["trend_daily"]["ema_gap_pct"] if r.get("option_type") == "CE" else -r["trend_daily"]["ema_gap_pct"])) >= 1.0)},
    {"name": "min_premium_10", "proposed": "2026-11-27",
     "desc": "no alert when the option premium is under Rs 10 (tiny premiums have big relative spreads and a stop worth only a few ticks)",
     "delta": lambda r: 0.0, "gate": lambda r: (r.get("entry") or 0) >= 10.0},
    {"name": "intraday_15m_must_agree", "proposed": "2026-11-27",
     "desc": "no alert unless the stock's 15-minute EMA9 vs EMA20 agrees with the trade (rows without intraday data pass)",
     "delta": lambda r: 0.0,
     "gate": lambda r: (not (r.get("intraday") or {}).get("ema_up_15m") is not None) or
                       (bool(r["intraday"]["ema_up_15m"]) == (r.get("option_type") == "CE"))},
    {"name": "window_0950_1430", "proposed": "2026-11-26",
     "desc": "alerts only between 09:50 and 14:30 (skip the opening half hour and the last stretch)",
     "delta": lambda r: 0.0, "gate": lambda r: "09:50" <= (r.get("entry_clock") or "") < "14:30"},
]


# ---- data checks -----------------------------------------------------------------------------------------------------
def row_issues(r):
    issues = []
    for f in REQUIRED_FIELDS:
        if r.get(f) is None:
            issues.append(f"missing {f}")
    if r.get("date") in STALE_VOLUME_DATES:
        issues.append("stale volume day")
    if not r.get("rules_version"):
        issues.append("rows from before rule versioning (score converted)")
    if (r.get("entry") or 0) <= 0:
        issues.append("bad entry price")
    if r.get("minutes_open") is not None and r["minutes_open"] < 10:
        issues.append("open under 10 min (too short to judge)")
    sil = getattr(config, "SILENT_TRACK_UNTIL", None)
    if sil and not r.get("alerted") and (r.get("entry_clock") or "") >= sil and r.get("date", "") >= "2026-11-27":
        issues.append("silent trade after the silent-tracking stop")
    parts = r.get("score_parts")
    if parts and r.get("score") is not None and r.get("rules_version"):
        try:
            calc = (screener.W_MOM * parts["mom"] + screener.W_VOL * parts["vol"] + screener.W_OI * parts["oi"]
                    + screener.W_IV * parts["iv"] - parts.get("penalty", 0) + parts.get("bonus", 0))
            if abs(calc - r["score"]) > 0.25:
                issues.append("score does not match its parts")
        except Exception:
            issues.append("score parts unreadable")
    if bool(r.get("win")) != ((r.get("a_pnl") or 0) > 0):
        issues.append("win flag disagrees with P&L")
    return issues


def data_checks(rows):
    """(counter of issues, list of duplicate keys, number of clean rows). 'stale volume day' and 'before versioning' are
    informational (kept out of out-of-sample evidence, not errors)."""
    counts = collections.Counter()
    clean = 0
    for r in rows:
        iss = row_issues(r)
        counts.update(iss)
        if not [i for i in iss if not i.startswith(("stale volume", "rows from before", "open under"))]:
            clean += 1
    keys = collections.Counter((r.get("date"), r.get("symbol"), r.get("strike"), r.get("option_type"), r.get("entry_clock"),
                                bool(r.get("alerted"))) for r in rows)
    dups = [k for k, n in keys.items() if n > 1]
    if dups:
        counts["duplicate rows"] += len(dups)
    return counts, dups, clean


def usable_for_evidence(r):
    """A row may count as out-of-sample evidence only if its data is clean and its volume factor was live."""
    bad = ("missing", "bad entry", "score does not match", "score parts unreadable", "stale volume", "win flag")
    return not any(i.startswith(bad) for i in row_issues(r))


# ---- replay ----------------------------------------------------------------------------------------------------------
def _alert_set(rows, cand=None):
    """Indexes of the rows that would have alerted under the live rules (cand=None) or live + candidate: the earliest qualifying
    row per (date, symbol), entered before the alert cutoff."""
    cutoff = config.NO_ALERTS_AFTER or "99:99"
    picked = {}
    for i, r in enumerate(rows):
        if r.get("manual") or (r.get("entry_clock") or "") >= cutoff:
            continue
        score = live_score(r) + (cand["delta"](r) if cand else 0.0)
        if score < config.ALERT_SCORE or (cand and not cand["gate"](r)):
            continue
        key = (r.get("date"), r.get("symbol"))
        if key not in picked or (r.get("entry_clock") or "") < (rows[picked[key]].get("entry_clock") or ""):
            picked[key] = i
    return set(picked.values())


def _summ(rows, idx):
    sel = [rows[i] for i in sorted(idx)]
    s = wr.stats(sel)
    return {"n": s["n"], "wins": s["wins"], "t1": sum(1 for r in sel if r.get("t1_reached")), "pnl": s["tot_a"],
            "pnl_b": s["tot_b"], "win_rate": s["win_rate"]}


def compare(rows, cand):
    live, alt = _alert_set(rows), _alert_set(rows, cand)
    dropped, added = live - alt, alt - live
    return {"live": _summ(rows, live), "cand": _summ(rows, alt),
            "dropped_winners": sum(1 for i in dropped if (rows[i].get("a_pnl") or 0) > 0),
            "avoided_losers": sum(1 for i in dropped if (rows[i].get("a_pnl") or 0) <= 0),
            "new_winners": sum(1 for i in added if (rows[i].get("a_pnl") or 0) > 0),
            "new_losers": sum(1 for i in added if (rows[i].get("a_pnl") or 0) <= 0),
            "dropped_names": [f"{rows[i]['symbol']} {money(rows[i].get('a_pnl') or 0)}" for i in sorted(dropped)][:6],
            "added_names": [f"{rows[i]['symbol']} {money(rows[i].get('a_pnl') or 0)}" for i in sorted(added)][:6]}


def money(x):
    return wr.money(x)


def split_samples(rows, proposed):
    ins = [r for r in rows if r.get("date", "") <= proposed]
    oos = [r for r in rows if r.get("date", "") > proposed and usable_for_evidence(r)]
    return ins, oos


# ---- reports -----------------------------------------------------------------------------------------------------------
def _fmt(s):
    return f"{s['n']} alerts, {s['wins']} wins, {s['t1']} reached T1, {money(s['pnl'])}"


def candidates_markdown(rows):
    L = ["## Candidate rules (the live rules do not change until the weekly meeting approves one)", "",
         "Only out-of-sample days (after the candidate was proposed, clean data, live volume) count as evidence.", ""]
    for c in CANDIDATES:
        ins, oos = split_samples(rows, c["proposed"])
        ci, co = compare(ins, c), compare(oos, c)
        L.append(f"### {c['name']} - {c['desc']} (proposed {c['proposed']})")
        L.append(f"- In-sample (shaped by these days, do not trust): live {_fmt(ci['live'])}; with the rule {_fmt(ci['cand'])}.")
        L.append(f"- **Out-of-sample:** live {_fmt(co['live'])}; with the rule {_fmt(co['cand'])}.")
        L.append(f"- Trade-off out-of-sample: drops {co['dropped_winners']} winner(s), avoids {co['avoided_losers']} loser(s), "
                 f"adds {co['new_winners']} winner(s) and {co['new_losers']} loser(s).")
        if co["live"]["n"] < MIN_OOS_ALERTS and co["cand"]["n"] < MIN_OOS_ALERTS:
            L.append(f"- Verdict: **too early** ({max(co['live']['n'], co['cand']['n'])} out-of-sample alerts; "
                     f"need about {MIN_OOS_ALERTS}).")
        else:
            better = co["cand"]["pnl"] - co["live"]["pnl"]
            L.append(f"- Verdict: {'better' if better > 0 else 'worse'} by {money(better)} out-of-sample; "
                     f"check the trade-off line before approving. One change per week.")
        L.append("")
    return L


def winners_vs_losers(rows):
    """What did the trades that reached Target 1 have that the others did not (one day: a hypothesis, not a rule)."""
    won = [r for r in rows if r.get("t1_reached")]
    rest = [r for r in rows if not r.get("t1_reached")]
    if not won:
        return ["- No trade reached Target 1 today."]
    feats = [("move 2%+ in the trade's direction", lambda r: moved_pct(r) >= 2.0),
             ("open interest agrees", oi_aligned), ("open interest against", oi_against),
             ("trend stretched", stretched), ("entered before 10:30", lambda r: (r.get("entry_clock") or "") < "10:30"),
             ("entered after 13:30", lambda r: (r.get("entry_clock") or "") >= "13:30")]
    L = [f"- {len(won)} of {len(rows)} trades reached Target 1: " + ", ".join(f"{r['symbol']} ({money(r.get('a_pnl') or 0)})" for r in won[:8]) + "."]
    for name, fn in feats:
        a, b = sum(1 for r in won if fn(r)), sum(1 for r in rest if fn(r))
        L.append(f"  - {name}: {a}/{len(won)} winners vs {b}/{len(rest)} others")
    return L


def daily_markdown(rows, day):
    today = [r for r in rows if r.get("date") == day]
    counts, dups, clean = data_checks(rows)
    L = [f"# Rule lab - {day}", "", f"Live rules: {config.RULES_VERSION}. Trades in the journal today: {len(today)}.", "",
         "## Data checks (all history)", ""]
    L.append(f"- Rows: {len(rows)}; clean rows: {clean}.")
    if counts:
        for k, n in counts.most_common():
            L.append(f"  - {k}: {n}")
    else:
        L.append("  - no issues found")
    L.append("")
    live = _alert_set(today)
    L += ["## Today under the live rules (replay)", "",
          f"- Would alert: {_fmt(_summ(today, live))}.",
          f"- Silent/other trades: {len(today) - len(live)}; reached T1 overall: {sum(1 for r in today if r.get('t1_reached'))}.", "",
          "## What today's Target 1 trades had (hypothesis only)", ""] + winners_vs_losers(today) + [""]
    L += ["## Candidate rules tonight (replay on today's trades)", ""]
    for c in CANDIDATES:
        cmp_ = compare(today, c)
        tag = "in-sample" if day <= c["proposed"] else "out-of-sample"
        L.append(f"- **{c['name']}** ({tag}): {_fmt(cmp_['cand'])} vs live {_fmt(cmp_['live'])}; "
                 f"drops {cmp_['dropped_names'] or 'none'} ({cmp_['dropped_winners']} winner(s), {cmp_['avoided_losers']} loser(s)); "
                 f"adds {cmp_['added_names'] or 'none'}.")
    L.append("")
    return "\n".join(L)


def telegram_note(rows, day):
    today = [r for r in rows if r.get("date") == day]
    live = _summ(today, _alert_set(today))
    counts, _, clean = data_checks(rows)
    issues = sum(n for k, n in counts.items() if not k.startswith(("stale volume", "rows from before", "open under")))
    lines = [f"\U0001F9EA <b>Nightly learning {day}</b>",
             f"Live rules {config.RULES_VERSION}: {live['n']} alerts, {live['wins']} wins, {live['t1']} reached T1, {money(live['pnl'])} (replay).",
             f"Silent/other trades: {len(today) - live['n']}; reached T1 overall: {sum(1 for r in today if r.get('t1_reached'))}."]
    best = None
    for c in CANDIDATES:
        _, oos = split_samples(rows, c["proposed"])
        if not oos:
            continue
        co = compare(oos, c)
        gain = co["cand"]["pnl"] - co["live"]["pnl"]
        if best is None or gain > best[1]:
            best = (c["name"], gain, co)
    if best:
        enough = max(best[2]["cand"]["n"], best[2]["live"]["n"]) >= MIN_OOS_ALERTS
        lines.append(f"Out-of-sample so far, best candidate: {best[0]} ({money(best[1])} vs live, "
                     f"{best[2]['cand']['n']} alerts) - "
                     f"{'enough data to discuss' if enough else 'too early to decide'}; the weekly meeting reviews it.")
    else:
        lines.append("No out-of-sample days yet - candidates start counting from the next clean day.")
    lines.append(f"Data checks: {issues} issue(s), {clean} of {len(rows)} rows clean. Full report: journal/lab/ on the journal-data branch.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal-dir", default="journal")
    ap.add_argument("--date", default=None)
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args()
    rows = wr.load(args.journal_dir)
    if not rows:
        print("[rule_lab] the journal is empty - nothing to learn from yet.")
        return 0
    day = args.date or max(r.get("date", "") for r in rows)
    out = os.path.join(args.journal_dir, "lab")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, f"{day}.md"), "w") as fh:
        fh.write(daily_markdown(rows, day))
    with open(os.path.join(out, "candidates.md"), "w") as fh:
        fh.write("\n".join(["# Rule lab - candidates (cumulative)", ""] + candidates_markdown(rows)))
    print(f"[rule_lab] wrote {out}/{day}.md and candidates.md ({len(rows)} trades)")
    if args.telegram and getattr(config, "NIGHTLY_LEARNING_TELEGRAM", True):
        from telegram_notifier import send_telegram_message
        send_telegram_message(telegram_note(rows, day))
    return 0


if __name__ == "__main__":
    sys.exit(main())
