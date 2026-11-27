"""
triggers.py
-----------
Named, explainable technical triggers. Both the live screener (screener.py)
and the historical dashboard (backtest_report.py) call these SAME functions,
so "why did this fire" means the same thing whether you're looking at
today's signal or a signal from three months ago.

Each detector returns (fired: bool, human_readable_reason: str).
"""

import pandas as pd

from indicators import ema


def resistance_breakout(highs: pd.Series, closes: pd.Series, lookback: int = 20) -> tuple[bool, str]:
    """Today's close breaks above the highest high of the prior `lookback` days."""
    if len(closes) < lookback + 2:
        return False, ""
    prior_high = highs.iloc[-(lookback + 1):-1].max()
    today_close = closes.iloc[-1]
    if today_close > prior_high:
        return True, f"broke above {lookback}-day resistance near {prior_high:.1f}"
    return False, ""


def support_breakdown(lows: pd.Series, closes: pd.Series, lookback: int = 20) -> tuple[bool, str]:
    """Today's close breaks below the lowest low of the prior `lookback` days
    -- the bearish mirror of resistance_breakout."""
    if len(closes) < lookback + 2:
        return False, ""
    prior_low = lows.iloc[-(lookback + 1):-1].min()
    today_close = closes.iloc[-1]
    if today_close < prior_low:
        return True, f"broke below {lookback}-day support near {prior_low:.1f}"
    return False, ""


def support_bounce(lows: pd.Series, closes: pd.Series, lookback: int = 20) -> tuple[bool, str]:
    """Price dipped near the prior `lookback`-day low and closed back above it
    -- a bullish reversal trigger."""
    if len(closes) < lookback + 2:
        return False, ""
    prior_low = lows.iloc[-(lookback + 1):-1].min()
    today_low = lows.iloc[-1]
    today_close = closes.iloc[-1]
    near_support = today_low <= prior_low * 1.01
    closed_above = today_close > prior_low
    if near_support and closed_above:
        return True, f"bounced off {lookback}-day support near {prior_low:.1f}"
    return False, ""


def resistance_rejection(highs: pd.Series, closes: pd.Series, lookback: int = 20) -> tuple[bool, str]:
    """Price popped near the prior `lookback`-day high and closed back below
    it -- a bearish reversal trigger, the mirror of support_bounce."""
    if len(closes) < lookback + 2:
        return False, ""
    prior_high = highs.iloc[-(lookback + 1):-1].max()
    today_high = highs.iloc[-1]
    today_close = closes.iloc[-1]
    near_resistance = today_high >= prior_high * 0.99
    closed_below = today_close < prior_high
    if near_resistance and closed_below:
        return True, f"rejected at {lookback}-day resistance near {prior_high:.1f}"
    return False, ""


def two_candle_confirmation(opens: pd.Series, closes: pd.Series, bullish: bool) -> tuple[bool, str]:
    """Last two candles both closed in the trade's direction (green-green for
    bullish, red-red for bearish) -- a simple momentum-confirmation filter
    traders use to avoid single-candle noise."""
    if len(closes) < 2 or len(opens) < 2:
        return False, ""
    last_two_green = all(closes.iloc[-i] > opens.iloc[-i] for i in (1, 2))
    last_two_red = all(closes.iloc[-i] < opens.iloc[-i] for i in (1, 2))
    if bullish and last_two_green:
        return True, "two consecutive bullish candle closes"
    if not bullish and last_two_red:
        return True, "two consecutive bearish candle closes"
    return False, ""


def ema_crossover(closes: pd.Series, bullish: bool) -> tuple[bool, str]:
    if len(closes) < 21:
        return False, ""
    e9 = ema(closes, 9)
    e20 = ema(closes, 20)
    crossed_up = e9.iloc[-2] <= e20.iloc[-2] and e9.iloc[-1] > e20.iloc[-1]
    crossed_down = e9.iloc[-2] >= e20.iloc[-2] and e9.iloc[-1] < e20.iloc[-1]
    if bullish and (crossed_up or e9.iloc[-1] > e20.iloc[-1]):
        return True, "daily EMA9 above EMA20 (bullish daily trend)"
    if not bullish and (crossed_down or e9.iloc[-1] < e20.iloc[-1]):
        return True, "daily EMA9 below EMA20 (bearish daily trend)"
    return False, ""


def collect_triggers(history: pd.DataFrame, bullish: bool) -> list[str]:
    """Runs every detector against a history DataFrame with
    CH_OPENING_PRICE / CH_TRADE_HIGH_PRICE / CH_TRADE_LOW_PRICE /
    CH_CLOSING_PRICE columns (oldest row first) and returns the reasons that
    fired, in priority order."""
    opens = pd.to_numeric(history.get("CH_OPENING_PRICE", pd.Series(dtype=float)), errors="coerce").dropna()
    highs = pd.to_numeric(history.get("CH_TRADE_HIGH_PRICE", pd.Series(dtype=float)), errors="coerce").dropna()
    lows = pd.to_numeric(history.get("CH_TRADE_LOW_PRICE", pd.Series(dtype=float)), errors="coerce").dropna()
    closes = pd.to_numeric(history.get("CH_CLOSING_PRICE", pd.Series(dtype=float)), errors="coerce").dropna()

    if bullish:
        directional_checks = (resistance_breakout(highs, closes), support_bounce(lows, closes))
    else:
        directional_checks = (support_breakdown(lows, closes), resistance_rejection(highs, closes))

    reasons = []
    for fired, reason in (ema_crossover(closes, bullish),) + directional_checks + (
        two_candle_confirmation(opens, closes, bullish),
    ):
        if fired and reason:
            reasons.append(reason)
    return reasons
