"""
backtest.py
-----------
IMPORTANT LIMITATION: free historical *option premium/OI* data for Indian
F&O is not readily available. This backtest validates the DIRECTIONAL logic
(RSI/EMA momentum + volume) against historical underlying prices, then
approximates the option premium outcome using a fixed delta assumption.
It does NOT replay real historical option chains.

Treat these results as a sanity check on "does the momentum signal have any
edge at all", not as a promise of real-money performance. The paper-trading
phase (paper_trader.py, run daily against live data for a few weeks) is the
real validation step before risking capital - it uses actual option premiums.
"""

from dataclasses import dataclass, field

import pandas as pd

from indicators import ema, rsi
import triggers as trig
import config

ASSUMED_DELTA = 0.5   # rough ATM option delta used to translate underlying
                      # % move into an approximate premium % move
LEVERAGE_FACTOR = 5.0
MAX_HOLD_DAYS = 5     # how many trading days forward we let a signal run


@dataclass
class BacktestResult:
    total_signals: int
    wins: int
    losses: int
    win_rate_pct: float
    avg_return_pct: float


@dataclass
class TradeRecord:
    """One historical signal + how it would have played out, used by
    backtest_report.py to draw markers on the price chart and build the
    trade table. This is the SAME approximation approach as
    backtest_momentum_signal (fixed delta/leverage on daily closes) -- see
    the module docstring for the limitation."""
    symbol: str
    entry_date: str
    exit_date: str
    direction: str          # BULLISH or BEARISH
    entry_underlying: float
    exit_underlying: float
    outcome: str             # "T1", "T2", "T3", "SL", "TIME"
    pnl_pct: float
    reasons: list = field(default_factory=list)


def backtest_momentum_signal(history: pd.DataFrame, lookback: int = 250) -> BacktestResult:
    """history must have a 'CH_CLOSING_PRICE' column, oldest row first."""
    closes = pd.to_numeric(history["CH_CLOSING_PRICE"], errors="coerce").dropna().reset_index(drop=True)
    if len(closes) < 40:
        return BacktestResult(0, 0, 0, 0.0, 0.0)

    r = rsi(closes)
    e9 = ema(closes, 9)
    e20 = ema(closes, 20)

    outcomes = []
    start = max(25, len(closes) - lookback)
    for i in range(start, len(closes) - 1):
        bullish = r.iloc[i] >= config.RSI_BULLISH_MIN and e9.iloc[i] > e20.iloc[i]
        bearish = r.iloc[i] <= config.RSI_BEARISH_MAX and e9.iloc[i] < e20.iloc[i]
        if not (bullish or bearish):
            continue

        entry_underlying = closes.iloc[i]
        next_close = closes.iloc[i + 1]
        underlying_move_pct = (next_close - entry_underlying) / entry_underlying * 100
        # Convert to an approximate premium move: long CE gains from up-moves,
        # long PE gains from down-moves, scaled by assumed delta and rough
        # leverage of ~ (underlying / premium) which we approximate as 5x.
        leverage_factor = 5.0
        directional_move = underlying_move_pct if bullish else -underlying_move_pct
        premium_move_pct = directional_move * ASSUMED_DELTA * leverage_factor

        if premium_move_pct >= config.TARGET_PCT:
            outcomes.append(config.TARGET_PCT)
        elif premium_move_pct <= -config.STOPLOSS_PCT:
            outcomes.append(-config.STOPLOSS_PCT)
        else:
            outcomes.append(premium_move_pct)

    if not outcomes:
        return BacktestResult(0, 0, 0, 0.0, 0.0)

    wins = sum(1 for o in outcomes if o > 0)
    losses = sum(1 for o in outcomes if o <= 0)
    return BacktestResult(
        total_signals=len(outcomes),
        wins=wins,
        losses=losses,
        win_rate_pct=round(100 * wins / len(outcomes), 1),
        avg_return_pct=round(sum(outcomes) / len(outcomes), 2),
    )


def backtest_detailed(symbol: str, history: pd.DataFrame, lookback: int = 250) -> list[TradeRecord]:
    """Walk-forward version used by backtest_report.py: for every historical
    signal day, look up to MAX_HOLD_DAYS ahead to see which level (T1/T2/T3/
    SL/TIME) would have been hit first, using the same fixed-delta premium
    approximation as backtest_momentum_signal. Also tags each signal with the
    same human-readable triggers the live screener would show."""
    dates = history.get("CH_TIMESTAMP", pd.Series(dtype=str)).reset_index(drop=True)
    closes = pd.to_numeric(history["CH_CLOSING_PRICE"], errors="coerce").reset_index(drop=True)
    valid = closes.notna()
    dates, closes = dates[valid].reset_index(drop=True), closes[valid].reset_index(drop=True)
    if len(closes) < 40:
        return []

    r = rsi(closes)
    e9 = ema(closes, 9)
    e20 = ema(closes, 20)

    records = []
    start = max(25, len(closes) - lookback)
    for i in range(start, len(closes) - 1):
        bullish = r.iloc[i] >= config.RSI_BULLISH_MIN and e9.iloc[i] > e20.iloc[i]
        bearish = r.iloc[i] <= config.RSI_BEARISH_MAX and e9.iloc[i] < e20.iloc[i]
        if not (bullish or bearish):
            continue

        entry_price = closes.iloc[i]
        reasons = trig.collect_triggers(history.iloc[:i + 1], bullish=bullish)

        outcome, exit_idx, pnl_pct = "TIME", min(i + MAX_HOLD_DAYS, len(closes) - 1), 0.0
        for j in range(i + 1, min(i + MAX_HOLD_DAYS, len(closes) - 1) + 1):
            underlying_move_pct = (closes.iloc[j] - entry_price) / entry_price * 100
            directional_move = underlying_move_pct if bullish else -underlying_move_pct
            premium_move_pct = directional_move * ASSUMED_DELTA * LEVERAGE_FACTOR

            if premium_move_pct <= -config.STOPLOSS_PCT:
                outcome, exit_idx, pnl_pct = "SL", j, -config.STOPLOSS_PCT
                break
            if premium_move_pct >= config.TARGET_PCT_T3:
                outcome, exit_idx, pnl_pct = "T3", j, config.TARGET_PCT_T3
                break
            if premium_move_pct >= config.TARGET_PCT_T2:
                outcome, exit_idx, pnl_pct = "T2", j, config.TARGET_PCT_T2
            elif premium_move_pct >= config.TARGET_PCT_T1 and outcome == "TIME":
                outcome, exit_idx, pnl_pct = "T1", j, config.TARGET_PCT_T1
        else:
            final_j = min(i + MAX_HOLD_DAYS, len(closes) - 1)
            underlying_move_pct = (closes.iloc[final_j] - entry_price) / entry_price * 100
            directional_move = underlying_move_pct if bullish else -underlying_move_pct
            pnl_pct = directional_move * ASSUMED_DELTA * LEVERAGE_FACTOR
            if outcome == "TIME":
                exit_idx = final_j

        records.append(TradeRecord(
            symbol=symbol,
            entry_date=str(dates.iloc[i]),
            exit_date=str(dates.iloc[exit_idx]),
            direction="BULLISH" if bullish else "BEARISH",
            entry_underlying=round(float(entry_price), 2),
            exit_underlying=round(float(closes.iloc[exit_idx]), 2),
            outcome=outcome,
            pnl_pct=round(pnl_pct, 2),
            reasons=reasons,
        ))
    return records
