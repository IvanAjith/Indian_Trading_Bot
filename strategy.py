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

from dataclasses import dataclass

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
    lo_score, lo_prob = config.MIN_SIGNAL_SCORE, 55.0   # floor: barely qualifies
    hi_score, hi_prob = 100.0, 88.0                     # ceiling: near-perfect score
    score = max(lo_score, min(score, hi_score))
    frac = (score - lo_score) / (hi_score - lo_score) if hi_score > lo_score else 0.0
    return round(lo_prob + frac * (hi_prob - lo_prob), 1)


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


def build_trade_idea(snapshot: UnderlyingSnapshot, screen_result: ScreenResult) -> TradeIdea | None:
    if screen_result.bias not in ("BULLISH", "BEARISH"):
        return None

    option_type = "CE" if screen_result.bias == "BULLISH" else "PE"
    leg = _pick_strike(snapshot, option_type)
    if leg is None or leg.ltp <= 0:
        return None

    entry = leg.ltp
    target1 = round(entry * (1 + config.TARGET_PCT_T1 / 100), 2)
    target2 = round(entry * (1 + config.TARGET_PCT_T2 / 100), 2)
    target3 = round(entry * (1 + config.TARGET_PCT_T3 / 100), 2)
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
    )


def position_size(capital: float, entry_premium: float, lot_size: int = 1) -> int:
    """How many lots to buy so max loss stays within MAX_CAPITAL_PER_TRADE_PCT."""
    risk_per_lot = entry_premium * (config.STOPLOSS_PCT / 100) * lot_size
    max_risk = capital * (config.MAX_CAPITAL_PER_TRADE_PCT / 100)
    if risk_per_lot <= 0:
        return 0
    lots = int(max_risk // risk_per_lot)
    return max(lots, 0)
