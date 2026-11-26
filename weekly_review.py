"""
weekly_review.py
----------------
Reads the trade journal (journal/trades.jsonl) and looks ACROSS trades:

  * overall results and the two plans (A: full scale-out, B: 1 lot sold at T1)
  * results by score band, time of entry, index vs stock, CE vs PE, volume, OI confirmation,
    agreement with NIFTY, and cost per lot
  * which warning factors (weak volume, against the market, chasing, late entry, ...) really go
    with losing trades
  * the most common reasons trades lost
  * SUGGESTED rule changes - only where there are enough trades to mean something

Nothing here changes the bot's rules by itself. A few trades can easily teach the wrong lesson, so the
review RECOMMENDS and a person approves (then the change is a one-line edit in config.py).

    python weekly_review.py --journal-dir journal --days 7
    python weekly_review.py --journal-dir journal --days 7 --no-telegram
"""

import argparse
import collections
import datetime as dt
import json
import os

import journal as jr

MIN_BUCKET = 8        # a group needs at least this many trades before it is called strong/weak
MIN_FACTOR = 5        # per side (with / without) for a warning-factor comparison
GAP = 15.0            # win-rate gap (percentage points) that counts as a real difference


def load(journal_dir: str) -> list:
    path = os.path.join(journal_dir, "trades.jsonl")
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                try:   # re-read every row with the CURRENT diagnosis rules, so factors added later also cover older trades
                    d = jr.diagnose(row)
                    row.update({k: d[k] for k in ("outcome", "outcome_label", "win", "risk_codes", "good_codes", "lesson")})
                except Exception:
                    pass
                rows.append(row)
    return rows


# ---- bucketing -------------------------------------------------------------------------------
def b_score(r):
    s = r.get("score") or 0
    return "70-74" if s < 75 else "75-79" if s < 80 else "80-89" if s < 90 else "90+"


def b_time(r):
    c = r.get("entry_clock") or ""
    return ("09:20-09:50" if c < "09:50" else "09:50-10:30" if c < "10:30" else
            "10:30-12:00" if c < "12:00" else "12:00-14:00" if c < "14:00" else "14:00+")


def b_volume(r):
    v = r.get("volume_ratio")
    return "unknown" if v is None else "under 2x" if v < 2 else "2-4x" if v < 4 else "4x or more"


def b_oi(r):
    return "OI confirmed" if r.get("oi_signal") in ("long_buildup", "short_buildup") else "no OI confirmation"


def b_market(r):
    n = r.get("nifty_chg_pct")
    if r.get("is_index") or n is None:
        return "n/a (index or unknown)"
    up = r["option_type"] == "CE"
    if (up and n >= 0.1) or (not up and n <= -0.1):
        return "with NIFTY"
    if (up and n <= -0.3) or (not up and n >= 0.3):
        return "against NIFTY"
    return "NIFTY flat"


def b_cost(r):
    c = r.get("cost_per_lot") or 0
    return "under 15k" if c < 15000 else "15k-25k" if c < 25000 else "25k-35k" if c < 35000 else "over 35k"


DIMENSIONS = [
    ("Score", b_score), ("Entry time", b_time), ("Instrument", lambda r: "index" if r.get("is_index") else "stock"),
    ("Direction", lambda r: r.get("option_type", "?")), ("Volume", b_volume), ("Open interest", b_oi),
    ("Market agreement", b_market), ("Cost per lot", b_cost),
]


def stats(rows: list) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0, "wins": 0, "win_rate": 0.0, "avg_a": 0.0, "tot_a": 0.0, "avg_b": 0.0, "tot_b": 0.0,
                "ret_a": 0.0, "ret_b": 0.0, "stop_b4_t1": 0.0}
    wins = sum(1 for r in rows if r.get("a_pnl", 0) > 0)
    tot_a = sum(r.get("a_pnl", 0) for r in rows)
    tot_b = sum(r.get("b_pnl", 0) for r in rows)
    cap_a = sum(r.get("a_capital", 0) for r in rows)
    cap_b = sum(r.get("b_capital", 0) for r in rows)
    return {"n": n, "wins": wins, "win_rate": 100 * wins / n, "avg_a": tot_a / n, "tot_a": tot_a,
            "avg_b": tot_b / n, "tot_b": tot_b, "ret_a": (100 * tot_a / cap_a) if cap_a else 0.0,
            "ret_b": (100 * tot_b / cap_b) if cap_b else 0.0,
            "stop_b4_t1": 100 * sum(1 for r in rows if r.get("outcome") == "STOP_BEFORE_T1") / n}


def money(x):
    return f"{'+' if x > 0 else '-' if x < 0 else ''}₹{abs(x):,.0f}"


def analyse(rows: list) -> dict:
    overall = stats(rows)
    groups = {}
    for dim, fn in DIMENSIONS:
        by = collections.defaultdict(list)
        for r in rows:
            by[fn(r)].append(r)
        groups[dim] = {k: stats(v) for k, v in sorted(by.items())}

    # warning factors: win rate with vs without
    factors = {}
    for code in jr.RISK_FACTORS:
        with_ = [r for r in rows if code in (r.get("risk_codes") or [])]
        without = [r for r in rows if code not in (r.get("risk_codes") or [])]
        factors[code] = {"with": stats(with_), "without": stats(without)}

    good_factors = {}
    for code in jr.GOOD_FACTORS:
        with_ = [r for r in rows if code in (r.get("good_codes") or [])]
        without = [r for r in rows if code not in (r.get("good_codes") or [])]
        good_factors[code] = {"with": stats(with_), "without": stats(without)}

    reasons = collections.Counter()
    for r in rows:
        if r.get("a_pnl", 0) < 0:
            reasons.update(r.get("risk_codes") or [])

    # suggestions - only with enough evidence
    suggestions = []
    for code, fs in factors.items():
        w, wo = fs["with"], fs["without"]
        if w["n"] >= MIN_FACTOR and wo["n"] >= MIN_FACTOR and (wo["win_rate"] - w["win_rate"]) >= GAP:
            suggestions.append({
                "type": "avoid", "factor": code,
                "evidence": f"{w['n']} trades with '{jr.RISK_FACTORS[code][0]}' won {w['win_rate']:.0f}% vs "
                            f"{wo['win_rate']:.0f}% without ({wo['n']} trades)",
                "action": jr.RISK_FACTORS[code][1]})
    for code, fs in good_factors.items():
        w, wo = fs["with"], fs["without"]
        if w["n"] >= MIN_FACTOR and wo["n"] >= MIN_FACTOR and (w["win_rate"] - wo["win_rate"]) >= GAP:
            suggestions.append({
                "type": "favour", "factor": code,
                "evidence": f"{w['n']} trades with '{jr.GOOD_FACTORS[code]}' won {w['win_rate']:.0f}% vs "
                            f"{wo['win_rate']:.0f}% without ({wo['n']} trades)",
                "action": "Consider favouring (or requiring) this: e.g. a score bonus or a filter - needs your approval."})
    for dim, by in groups.items():
        for k, s in by.items():
            if s["n"] >= MIN_BUCKET and overall["n"] >= 2 * MIN_BUCKET:
                if s["avg_a"] < 0 and s["win_rate"] <= overall["win_rate"] - GAP:
                    suggestions.append({"type": "weak group", "factor": f"{dim}: {k}",
                                        "evidence": f"{s['n']} trades, win rate {s['win_rate']:.0f}% (overall {overall['win_rate']:.0f}%), "
                                                    f"average {money(s['avg_a'])} per trade",
                                        "action": f"Consider skipping or tightening signals in this group ({dim}: {k})."})
                elif s["avg_a"] > 0 and s["win_rate"] >= overall["win_rate"] + GAP:
                    suggestions.append({"type": "strong group", "factor": f"{dim}: {k}",
                                        "evidence": f"{s['n']} trades, win rate {s['win_rate']:.0f}% (overall {overall['win_rate']:.0f}%), "
                                                    f"average {money(s['avg_a'])} per trade",
                                        "action": f"This group does well ({dim}: {k}) - consider favouring it (e.g. a higher score floor if it is a score band)."})
    return {"overall": overall, "groups": groups, "factors": factors, "good_factors": good_factors,
            "reasons": reasons, "suggestions": suggestions}


def missed_winners(rows: list) -> dict:
    """Silent trades (never alerted) that reached Target 1: what did they have that the other silent trades did not?
    This is how the review learns which signals we would have wanted to alert on. Small samples = hypotheses only."""
    silent = [r for r in rows if not r.get("alerted", True) and not r.get("manual")]
    won = [r for r in silent if r.get("t1_reached")]
    rest = [r for r in silent if not r.get("t1_reached")]
    codes = {}
    for code in list(jr.GOOD_FACTORS) + list(jr.RISK_FACTORS):
        a = sum(1 for r in won if code in (r.get("good_codes") or []) + (r.get("risk_codes") or []))
        b = sum(1 for r in rest if code in (r.get("good_codes") or []) + (r.get("risk_codes") or []))
        if a or b:
            codes[code] = (a, len(won), b, len(rest))
    return {"won": won, "rest": rest, "codes": codes}


def _name(code: str) -> str:
    return jr.GOOD_FACTORS.get(code) or jr.RISK_FACTORS[code][0]


# ---- output ----------------------------------------------------------------------------------
def _table(by: dict) -> list:
    lines = ["| Group | Trades | Win rate | Avg P&L (A) | Total (A) | Avg P&L (B) |", "|---|---|---|---|---|---|"]
    for k, s in by.items():
        lines.append(f"| {k} | {s['n']} | {s['win_rate']:.0f}% | {money(s['avg_a'])} | {money(s['tot_a'])} | {money(s['avg_b'])} |")
    return lines


def split(rows):
    """(alerted trades, silent trades) - silent ones were tracked below the alert score and never sent to you."""
    return ([r for r in rows if r.get("alerted", True)], [r for r in rows if not r.get("alerted", True)])


def render(week_rows, all_rows, period: str) -> str:
    a_week, a_all = analyse(week_rows), analyse(all_rows)
    wk_alert, wk_silent = split(week_rows)
    al_alert, al_silent = split(all_rows)
    o, oa = stats(wk_alert), stats(al_alert)
    L = [f"# Weekly review - {period}", ""]
    L.append(f"**Your alerted trades, this period:** {o['n']} trades, {o['wins']} wins ({o['win_rate']:.0f}%). "
             f"Plan A (full scale-out) {money(o['tot_a'])} ({o['ret_a']:+.1f}% on the money invested); "
             f"Plan B (1 lot at T1) {money(o['tot_b'])} ({o['ret_b']:+.1f}%). "
             f"Before brokerage and taxes.")
    L.append(f"**Alerted trades, all history:** {oa['n']} trades, {oa['win_rate']:.0f}% win rate, "
             f"Plan A {money(oa['tot_a'])} ({oa['ret_a']:+.1f}%), Plan B {money(oa['tot_b'])} ({oa['ret_b']:+.1f}%).")
    if al_silent:
        ss = stats(al_silent)
        L.append(f"**Silent trades (below the alert score, never sent to you), all history:** {ss['n']} trades, "
                 f"{ss['win_rate']:.0f}% win rate, Plan A {money(ss['tot_a'])}. "
                 f"Compare with the alerted win rate above: if they are close, the alert score can come down; "
                 f"if silent is clearly worse, it is doing its job.")
    if oa["n"] < 30:
        L.append(f"\n> Only {oa['n']} trades so far. Treat everything below as a hint, not a conclusion "
                 f"(groups need at least {MIN_BUCKET} trades before they are flagged).")
    L += ["", "## Results by group (all history)", ""]
    for dim, by in a_all["groups"].items():
        L += [f"### {dim}", ""] + _table(by) + [""]
    L += ["## Do the warning factors really go with losses?", "",
          "| Factor | With it: trades / win rate | Without it: trades / win rate |", "|---|---|---|"]
    for code, fs in a_all["factors"].items():
        w, wo = fs["with"], fs["without"]
        L.append(f"| {jr.RISK_FACTORS[code][0]} | {w['n']} / {w['win_rate']:.0f}% | {wo['n']} / {wo['win_rate']:.0f}% |")
    L += ["", "## Do the good factors really go with wins?", "",
          "| Factor | With it: trades / win rate | Without it: trades / win rate |", "|---|---|---|"]
    for code, fs in a_all["good_factors"].items():
        w, wo = fs["with"], fs["without"]
        L.append(f"| {jr.GOOD_FACTORS[code]} | {w['n']} / {w['win_rate']:.0f}% | {wo['n']} / {wo['win_rate']:.0f}% |")
    mw = missed_winners(all_rows)
    L += ["", "## Silent trades that reached Target 1 - the ones we did not alert (what to learn)", ""]
    if mw["won"]:
        for r in sorted(mw["won"], key=lambda r: (r.get("date", ""), r.get("entry_clock", ""))):
            L.append(f"- {r.get('date')} {r.get('entry_clock')} {r['symbol']} {r['option_type']} {r['strike']:g}, score {r.get('score')}: "
                     f"peak {r.get('peak_gain_pct')}%, result {money(r.get('a_pnl', 0))}; "
                     f"with: {', '.join(_name(c) for c in (r.get('good_codes') or [])) or '-'}; "
                     f"against: {', '.join(_name(c) for c in (r.get('risk_codes') or [])) or '-'}.")
        L += ["", f"What they share versus the other {len(mw['rest'])} silent trades "
                  f"(count among the {len(mw['won'])} winners vs among the others):", ""]
        for code, (a, na, b, nb) in sorted(mw["codes"].items(), key=lambda kv: -(kv[1][0] / max(kv[1][1], 1) - kv[1][2] / max(kv[1][3], 1))):
            L.append(f"- {_name(code)}: {a}/{na} winners vs {b}/{nb} others")
        L.append("\n> Few trades: these are hypotheses to test with more data, not rules.")
    else:
        L.append("- none yet")
    L += ["", "## Most common warning signs in losing trades (this period)", ""]
    if a_week["reasons"]:
        L += [f"- {jr.RISK_FACTORS[c][0]}: {n} losing trade(s)" for c, n in a_week["reasons"].most_common()]
    else:
        L.append("- none recorded")
    L += ["", "## Suggested changes (need your approval - nothing changes automatically)", ""]
    if a_all["suggestions"]:
        for s in a_all["suggestions"]:
            L += [f"- **{s['factor']}** ({s['type']}): {s['evidence']}.", f"  → {s['action']}"]
    else:
        L.append("- No change suggested yet - not enough evidence, or no clear difference.")
    L.append("")
    return "\n".join(L)


def digest(week_rows, all_rows, period: str) -> str:
    a_week, a_all = analyse(week_rows), analyse(all_rows)
    o, oa = stats(split(week_rows)[0]), stats(split(all_rows)[0])
    ss = stats(split(all_rows)[1])
    lines = [f"📚 <b>Weekly review - {period}</b>",
             f"Your alerted trades this period: {o['n']}, win rate {o['win_rate']:.0f}%, Plan A {money(o['tot_a'])} "
             f"({o['ret_a']:+.1f}% on money invested), Plan B {money(o['tot_b'])} ({o['ret_b']:+.1f}%).",
             f"All history: {oa['n']} alerted trades, win rate {oa['win_rate']:.0f}%."]
    if ss["n"]:
        lines.append(f"Silent (below alert score): {ss['n']} trades, win rate {ss['win_rate']:.0f}% - "
                     f"{'close to the alerted rate: the alert score may be too high' if ss['n'] >= 8 and ss['win_rate'] >= oa['win_rate'] - 5 else 'compare with the alerted rate'}.")
    if a_week["reasons"]:
        top = ", ".join(f"{jr.RISK_FACTORS[c][0]} ({n})" for c, n in a_week["reasons"].most_common(3))
        lines.append(f"Most common warning signs in losers: {top}.")
    mw = missed_winners(all_rows)
    if mw["won"]:
        names = ", ".join(f"{r['symbol']}" for r in mw["won"][:6])
        lines.append(f"Silent trades that reached Target 1 (not alerted): {len(mw['won'])} of {len(mw['won']) + len(mw['rest'])} - {names}. "
                     f"See the report for what they had in common.")
    if oa["n"] < 30:
        lines.append(f"<i>Only {oa['n']} trades so far - hints, not conclusions.</i>")
    if a_all["suggestions"]:
        lines.append("\n<b>Suggested changes (your approval needed):</b>")
        for s in a_all["suggestions"][:4]:
            lines.append(f"• {s['factor']}: {s['evidence']}.")
    else:
        lines.append("No rule change suggested yet.")
    lines.append("\nFull report: journal/weekly/ on the journal-data branch.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal-dir", default="journal")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--today", default=None, help="YYYY-MM-DD (testing)")
    args = ap.parse_args()

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    start = today - dt.timedelta(days=args.days - 1)
    all_rows = load(args.journal_dir)
    week_rows = [r for r in all_rows if start.isoformat() <= r.get("date", "") <= today.isoformat()]
    period = f"{start:%d %b} to {today:%d %b %Y}"
    print(f"[weekly_review] {len(week_rows)} trades this period, {len(all_rows)} in total")
    if not all_rows:
        print("[weekly_review] the journal is empty - nothing to review yet.")
        return

    report = render(week_rows, all_rows, period)
    out_dir = os.path.join(args.journal_dir, "weekly")
    os.makedirs(out_dir, exist_ok=True)
    iso = today.isocalendar()
    path = os.path.join(out_dir, f"{iso[0]}-W{iso[1]:02d}.md")
    with open(path, "w") as fh:
        fh.write(report)
    with open(os.path.join(args.journal_dir, "learnings.json"), "w") as fh:
        json.dump({"generated": today.isoformat(), "trades": len(all_rows),
                   "suggestions": analyse(all_rows)["suggestions"]}, fh, indent=2, ensure_ascii=False)
    print(f"[weekly_review] wrote {path}")

    if not args.no_telegram:
        from telegram_notifier import send_telegram_message
        send_telegram_message(digest(week_rows, all_rows, period))


if __name__ == "__main__":
    main()
