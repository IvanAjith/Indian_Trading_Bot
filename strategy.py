"""
strategy.py
-----------
Turns a ScreenResult (bias + score) into a concrete tradeable idea:
which option, what strike, what expiry, entry/target/stoploss.

Target/stoploss are set as a fixed % of the option PREMIUM (config.TARGET_PCT
/ STOPLOSS_PCT), which is the simplest, most transparent approach for weekly
options - it sidesteps needing an accurate options pricing model to translate
an underlying price target into a premium target. It also means risk per
trade is known and bounded up front, which matters more than precision here.
"""

from dataclasses import dataclass, field

from data_fetcher import UnderlyingSnapshot, OptionQuote
from screener import ScreenResult
import config


@dataclass
class TradeIdea:
    symbol: str
    option_type: str     # CE or PE
    strike: float
    expiry: str
    bias: str
    entry: float
    target1: float
    target2: float
    target3: float
    stoploss: float
    score: float
    rationale: str
    reasons: list         # explicit technical triggers (breakout, OI, etc.)
    lot_size: int = 1               # units per lot, from the option contract
    n_targets: int = 3              # 3 for indexes, 2 for stocks (target3 is unused when 2)
    context: dict = field(default_factory=dict)   # what the market looked like at entry (for the journal)
    win_probability: float = 0.0   # heuristic estimate, see estimate_win_probability()

    @property
    def target(self) -> float:
        """Backward-compat alias -- old code/CSV logs used a single target."""
        return self.target2


def estimate_win_probability(score: float) -> float:
    """Rough heuristic mapping from the composite screener score (0-100) to
    an ESTIMATED probability of hitting Target 1 before the stoploss.

    This is NOT a backtested/calibrated probability - it's a linear
    stretch of the score into a plausible-looking range, purely so you
    have a quick confidence read alongside the score. The score itself is
    a rules-quality metric (how many technical conditions lined up), not a
    measured win rate.

    For a REAL, empirical win rate, look at:
      - paper_trades.csv / PaperTradingAccount.summary()["win_rate_pct"]
        after you've run enough paper trades, or
      - backtest_report.html's win_rate_pct per symbol.
    Those are actual outcomes; this number is not.
    """
    lo_score, lo_prob = config.ALERT_SCORE, 55.0   # floor: barely qualifies for an alert
    hi_score, hi_prob = 100.0, 88.0                     # ceiling: near-perfect score
    score = max(lo_score, min(score, hi_score))
    frac = (score - lo_score) / (hi_score - lo_score) if hi_score > lo_score else 0.0
    return round(lo_prob + frac * (hi_prob - lo_prob), 1)


def is_index(symbol: str) -> bool:
    return symbol.upper() in {s.upper() for s in config.INDEX_UNIVERSE}


def plan_for(symbol: str):
    """(lots, number of targets) for a trade on this underlying: indexes 3 lots / 3 targets,
    stocks 2 lots / 2 targets (see config.py). lots is None when sizing by the risk rule."""
    if is_index(symbol):
        return config.LOTS_PER_TRADE_INDEX, int(config.TARGETS_INDEX)
    return config.LOTS_PER_TRADE_STOCK, int(config.TARGETS_STOCK)


def _pick_strike(snapshot: UnderlyingSnapshot, option_type: str) -> OptionQuote | None:
    candidates = [q for q in snapshot.option_chain if q.option_type == option_type]
    if not candidates:
        return None
    # Sort strikes ascending, find ATM index, then apply STRIKE_OFFSET.
    strikes = sorted(set(q.strike for q in candidates))
    atm_strike = min(strikes, key=lambda s: abs(s - snapshot.spot))
    atm_idx = strikes.index(atm_strike)
    offset = config.STRIKE_OFFSET if option_type == "CE" else -config.STRIKE_OFFSET
    target_idx = min(max(atm_idx + offset, 0), len(strikes) - 1)
    target_strike = strikes[target_idx]
    return next(q for q in candidates if q.strike == target_strike)


def _avg_daily_range(snapshot: UnderlyingSnapshot, days: int = 14) -> float:
    """Average daily high-low range (price points) of the underlying over the last `days` completed days (ATR-like)."""
    h = snapshot.history
    try:
        if h is None or h.empty:
            return 0.0
        import datetime as _dt
        if "CH_TIMESTAMP" in h and str(h["CH_TIMESTAMP"].iloc[-1]) == _dt.date.today().isoformat() and len(h) > 1:
            h = h.iloc[:-1]
        rng = (h["CH_TRADE_HIGH_PRICE"] - h["CH_TRADE_LOW_PRICE"]).tail(days)
        return float(rng.mean())
    except Exception:
        return 0.0


def _estimate_delta(side: list, i: int, option_type: str) -> float:
    """Delta of side[i] (quotes of ONE option type sorted by strike), estimated from neighbouring strikes:
    how much the premium changes per point of strike. 0 if it cannot be estimated."""
    lo, hi = max(i - 1, 0), min(i + 1, len(side) - 1)
    if hi == lo:
        return 0.0
    dk = side[hi].strike - side[lo].strike
    if dk <= 0:
        return 0.0
    dp = side[lo].ltp - side[hi].ltp if option_type == "CE" else side[hi].ltp - side[lo].ltp
    return max(0.0, min(1.0, dp / dk))


def pick_reasoned_strike(snapshot: UnderlyingSnapshot, option_type: str, exclude_strikes=(), sides: str = "otm",
                         max_cost: float = None):
    """Choose a strike by REASONS (see config 'Reasoned strike choice'), not just "whatever fits the budget".
    sides: "otm" = at/out of the money only (cheaper strikes); "any" = also in-the-money (used when a repeat call must
    avoid an earlier strike). Returns {"leg": OptionQuote, "reasons": [str, ...], "delta": float, "steps": int} or None.
    Rule of thumb applied last: of everything that passes the checks, take the one CLOSEST to the money."""
    max_cost = config.MAX_COST_PER_LOT if max_cost is None else max_cost
    atm = _pick_strike(snapshot, option_type)
    if atm is None:
        return None
    side = sorted((q for q in snapshot.option_chain if q.option_type == option_type and q.expiry == atm.expiry),
                  key=lambda q: q.strike)
    if len(side) < 2:
        return None
    strikes = [q.strike for q in side]
    atm_i = strikes.index(atm.strike)
    atr = _avg_daily_range(snapshot)
    atm_delta = _estimate_delta(side, atm_i, option_type)
    other_type = "PE" if option_type == "CE" else "CE"
    exclude = {float(x) for x in exclude_strikes}

    # opposing open-interest wall: for a CE the biggest CE-OI strike above the spot (resistance); for a PE the biggest
    # PE-OI strike below the spot (support)
    wall_strike, wall_oi = None, 0
    pool = [q for q in side if (q.strike > snapshot.spot if option_type == "CE" else q.strike < snapshot.spot)]
    if pool:
        w = max(pool, key=lambda q: q.oi or 0)
        wall_strike, wall_oi = w.strike, (w.oi or 0)

    best = None
    for i, q in enumerate(side):
        if q.strike in exclude or q.ltp <= 0:
            continue
        steps = (i - atm_i) if option_type == "CE" else (atm_i - i)   # >0 = out of the money
        if sides == "otm" and steps < 0:
            continue
        if abs(steps) > config.STRIKE_MAX_STEPS:
            continue          # never drift far from ATM just to get under the budget
        lot = max(int(q.lot_size or 1), 1)
        cost = q.ltp * lot
        if max_cost and cost > max_cost:
            continue
        delta = _estimate_delta(side, i, option_type)
        if delta < config.STRIKE_MIN_DELTA:
            continue
        if atm_delta > 0 and delta < config.STRIKE_MIN_DELTA_VS_ATM * atm_delta:
            continue          # must still behave like the at-the-money trade
        move = (q.ltp * config.TARGET_PCT_T1 / 100) / delta if delta > 0 else float("inf")
        if atr > 0 and move > config.STRIKE_MAX_MOVE_ATR_FRAC * atr:
            continue
        if (q.volume or 0) < config.STRIKE_MIN_VOLUME or (q.oi or 0) < config.STRIKE_MIN_OI:
            continue
        if (wall_strike is not None and wall_oi >= config.STRIKE_OI_WALL_RATIO * max(q.oi or 0, 1)
                and ((option_type == "CE" and q.strike >= wall_strike) or (option_type == "PE" and q.strike <= wall_strike))):
            continue
        cand = (abs(steps), -delta)          # closest to the money first, then highest delta
        if best is None or cand < best[0]:
            best = (cand, q, steps, delta, move, cost)
    if best is None:
        return None
    _, q, steps, delta, move, cost = best
    where = "at the money" if steps == 0 else (f"{abs(steps)} strike(s) {'out of' if steps > 0 else 'in'} the money")
    reasons = [
        f"cost ₹{cost:,.0f} per lot (limit ₹{max_cost:,.0f})" if max_cost else f"cost ₹{cost:,.0f} per lot",
        where,
        f"delta about {delta:.2f}" + (f" ({delta / atm_delta * 100:.0f}% of the at-the-money delta)" if atm_delta > 0 else ""),
        (f"needs about {move:,.1f} points for Target 1 ({move / atr * 100:.0f}% of the usual daily range {atr:,.0f})"
         if atr > 0 else f"needs about {move:,.1f} points for Target 1"),
        f"volume {int(q.volume or 0):,}, open interest {int(q.oi or 0):,}",
        "no big opposing open-interest wall in the way",
    ]
    return {"leg": q, "reasons": reasons, "delta": round(delta, 2), "steps": steps}


def build_trade_idea(snapshot: UnderlyingSnapshot, screen_result: ScreenResult, leg: OptionQuote = None,
                     strike_pick: dict = None) -> TradeIdea | None:
    """leg / strike_pick: use this contract instead of the default at-the-money one (see pick_reasoned_strike)."""
    if screen_result.bias not in ("BULLISH", "BEARISH"):
        return None

    option_type = "CE" if screen_result.bias == "BULLISH" else "PE"
    if leg is None:
        leg = _pick_strike(snapshot, option_type)
    if leg is None or leg.ltp <= 0:
        return None

    entry = leg.ltp
    target1 = round(entry * (1 + config.TARGET_PCT_T1 / 100), 2)
    target2 = round(entry * (1 + config.TARGET_PCT_T2 / 100), 2)
    n_targets = plan_for(snapshot.symbol)[1]
    target3 = round(entry * (1 + config.TARGET_PCT_T3 / 100), 2) if n_targets >= 3 else 0.0
    stoploss = round(entry * (1 - config.STOPLOSS_PCT / 100), 2)

    reasons = screen_result.trigger_reasons or [
        f"RSI {screen_result.rsi_value}", f"OI: {screen_result.oi_signal}",
    ]
    rationale = (
        f"{screen_result.bias.title()} bias -- " + "; ".join(reasons) +
        f". (score {screen_result.score}/100, IV pctile {screen_result.iv_pct})"
    )

    return TradeIdea(
        symbol=snapshot.symbol,
        option_type=option_type,
        strike=leg.strike,
        expiry=leg.expiry,
        bias=screen_result.bias,
        entry=entry,
        target1=target1,
        target2=target2,
        target3=target3,
        stoploss=stoploss,
        score=screen_result.score,
        rationale=rationale,
        reasons=reasons,
        win_probability=estimate_win_probability(screen_result.score),
        lot_size=max(int(leg.lot_size or 1), 1),
        n_targets=n_targets,
        context={
            "rsi": screen_result.rsi_value,
            "volume_ratio": screen_result.volume_ratio,
            "oi_signal": screen_result.oi_signal,
            "iv_pct": screen_result.iv_pct,
            "spot": snapshot.spot,
            "spot_chg_pct": round((snapshot.spot / snapshot.prev_close - 1) * 100, 2) if snapshot.prev_close else None,
            "is_index": is_index(snapshot.symbol),
            "cost_per_lot": round(entry * max(int(leg.lot_size or 1), 1), 2),
            "score_parts": dict(getattr(screen_result, "parts", {}) or {}),
            "rules_version": config.RULES_VERSION,
            **({"strike_pick": list(strike_pick["reasons"]), "variant": "reasoned_strike"} if strike_pick else {}),
        },
    )


def position_size(capital: float, entry_premium: float, lot_size: int = 1) -> int:
    """How many lots to buy so (a) the loss at the stoploss stays within
    MAX_CAPITAL_PER_TRADE_PCT of capital and (b) you can afford the premium.
    Returns 0 if even ONE lot is too risky/expensive for this capital - the
    trade is then skipped rather than oversized."""
    cost_per_lot = entry_premium * lot_size
    risk_per_lot = cost_per_lot * (config.STOPLOSS_PCT / 100)
    max_risk = capital * (config.MAX_CAPITAL_PER_TRADE_PCT / 100)
    if risk_per_lot <= 0:
        return 0
    lots = min(int(max_risk // risk_per_lot), int(capital // cost_per_lot))
    return max(lots, 0)
