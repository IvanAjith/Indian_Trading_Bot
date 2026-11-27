"""
levels.py
---------
Support and resistance levels of an underlying, for the journal and the nightly rule lab (information only - they do not change any score yet).

key_levels(history, spot) uses the DAILY history: the 20-day high/low, the previous day's high/low/close, classic pivot points computed from the
previous day (P, R1, S1, R2, S2) and the nearest round-number levels. It also reports the distance (in %) from the spot to the nearest level above and
below, which is the "room to run" for a call (to the next resistance) or a put (to the next support).
"""

import datetime as dt
import math

import pandas as pd


def _nice_step(spot: float) -> float:
    """A round-number step of about 0.5% of the price, on the 1-2-5 scale (e.g. NIFTY at 25,850 -> 200; a Rs 1,900 stock -> 10)."""
    raw = spot * 0.005
    if raw <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def key_levels(history: pd.DataFrame, spot: float) -> dict:
    """Levels from daily candles. Empty dict if there is not enough history."""
    try:
        if history is None or history.empty or len(history) < 22:
            return {}
        h = history
        today = dt.date.today().isoformat()
        if "CH_TIMESTAMP" in h and str(h["CH_TIMESTAMP"].iloc[-1]) >= today:
            h = h.iloc[:-1]                      # drop today's (partial) row: levels come from COMPLETED days
        if len(h) < 21:
            return {}
        last = h.iloc[-1]
        pdh, pdl, pdc = float(last["CH_TRADE_HIGH_PRICE"]), float(last["CH_TRADE_LOW_PRICE"]), float(last["CH_CLOSING_PRICE"])
        hi20 = float(h["CH_TRADE_HIGH_PRICE"].tail(20).max())
        lo20 = float(h["CH_TRADE_LOW_PRICE"].tail(20).min())
        p = (pdh + pdl + pdc) / 3
        r1, s1 = 2 * p - pdl, 2 * p - pdh
        r2, s2 = p + (pdh - pdl), p - (pdh - pdl)
        step = _nice_step(spot)
        round_up = math.floor(spot / step) * step + step
        round_dn = math.ceil(spot / step) * step - step
        levels = {"hi20": hi20, "lo20": lo20, "pdh": pdh, "pdl": pdl, "pdc": pdc, "pivot": p, "r1": r1, "s1": s1, "r2": r2, "s2": s2,
                  "round_up": round_up, "round_dn": round_dn}
        above = [v for v in levels.values() if v > spot * 1.0005]
        below = [v for v in levels.values() if v < spot * 0.9995]
        out = {k: round(v, 2) for k, v in levels.items()}
        out["next_res"] = round(min(above), 2) if above else None
        out["next_sup"] = round(max(below), 2) if below else None
        out["room_up_pct"] = round((min(above) / spot - 1) * 100, 2) if above else None      # room for a CALL
        out["room_down_pct"] = round((1 - max(below) / spot) * 100, 2) if below else None    # room for a PUT
        return out
    except Exception:
        return {}
