"""
screener.py
-----------
Scores every symbol in the universe on a handful of signals and ranks them.
Nothing here promises a winning trade - it's a rules-based filter to narrow
~40 stocks down to a shortlist worth looking at, same as a human scanning a
watchlist by hand would, just faster and unemotional.

Score components (each 0-100, then weighted-averaged):
  - momentum:      RSI + EMA9/EMA20 crossover direction & distance
  - volume_spike:  today's volume vs 20-day average
  - oi_buildup:    option OI change lining up with price direction
                    (long buildup = price up + OI up -> bullish conviction;
                     short buildup = price down + OI up -> bearish conviction)
  - iv_percentile: prefer moderate-to-high IV (options have room to move)
                    but flag extreme IV (>90th pct) as "expensive/event risk"
"""

from dataclasses import dataclass

import pandas as pd

from indicators import ema, rsi, iv_percentile
from data_fetcher import UnderlyingSnapshot
import triggers as trig
import config


@dataclass
class ScreenResult:
    symbol: str
    bias: str            # "BULLISH", "BEARISH", or "NEUTRAL"
    score: float          # 0-100 composite
    spot: float
    rsi_value: float
    volume_ratio: float
    oi_signal: str        # e.g. "long_buildup", "short_buildup", "unwinding"
    iv_pct: float
    trigger_reasons: list  # human-readable technical triggers that fired
    notes: str


def _momentum_score(closes: pd.Series) -> tuple[float, str, float]:
    if len(closes) < 25:
        return 50.0, "NEUTRAL", 50.0
    r = rsi(closes).iloc[-1]
    e9 = ema(closes, 9).iloc[-1]
    e20 = ema(closes, 20).iloc[-1]
    trend_up = e9 > e20
    if r >= config.RSI_BULLISH_MIN and trend_up:
        bias = "BULLISH"
        score = min(100.0, 50 + (r - config.RSI_BULLISH_MIN) * 2)
    elif r <= config.RSI_BEARISH_MAX and not trend_up:
        bias = "BEARISH"
        score = min(100.0, 50 + (config.RSI_BEARISH_MAX - r) * 2)
    else:
        bias = "NEUTRAL"
        score = 40.0
    return score, bias, float(r)


def _day_share(now=None) -> float:
    """Share of a normal day's volume usually traded by this IST time (config.VOLUME_DAY_PROFILE), 0..1."""
    import datetime as _dt
    now = now or _dt.datetime.now()
    cur = now.hour * 60 + now.minute
    pts = [(int(t[:2]) * 60 + int(t[3:]), f) for t, f in config.VOLUME_DAY_PROFILE]
    if cur <= pts[0][0]:
        return pts[0][1]
    for (t0, f0), (t1, f1) in zip(pts, pts[1:]):
        if cur <= t1:
            return f0 + (f1 - f0) * (cur - t0) / (t1 - t0)
    return pts[-1][1]


def _volume_score(day_volume: int, avg_20d: float, now=None) -> tuple[float, float]:
    if avg_20d <= 0:
        return 50.0, 1.0
    if getattr(config, "VOLUME_TIME_ADJUST", False):
        share = max(_day_share(now), config.VOLUME_PROFILE_MIN_SHARE)
        ratio = day_volume / (avg_20d * share)
    else:
        ratio = day_volume / avg_20d
    score = min(100.0, max(0.0, (ratio - 1) * 60 + 40))
    return score, ratio


def _oi_signal(snapshot: UnderlyingSnapshot, bias_hint: str) -> tuple[str, float]:
    # Use ATM strike OI change as a proxy for "where the crowd is positioned".
    if not snapshot.option_chain:
        return "unknown", OI_SCORE_UNKNOWN
    atm = min(snapshot.option_chain, key=lambda q: abs(q.strike - snapshot.spot))
    same_strike_legs = [q for q in snapshot.option_chain if q.strike == atm.strike]
    ce = next((q for q in same_strike_legs if q.option_type == "CE"), None)
    pe = next((q for q in same_strike_legs if q.option_type == "PE"), None)
    price_up = snapshot.spot >= snapshot.prev_close

    if ce and ce.oi_change_pct >= config.MIN_OI_CHANGE_PCT and price_up:
        return "long_buildup", OI_SCORE_MAX
    if pe and pe.oi_change_pct >= config.MIN_OI_CHANGE_PCT and not price_up:
        return "short_buildup", OI_SCORE_MAX
    if ce and ce.oi_change_pct <= -config.MIN_OI_CHANGE_PCT and not price_up:
        return "long_unwinding", 55.0
    if pe and pe.oi_change_pct <= -config.MIN_OI_CHANGE_PCT and price_up:
        return "short_covering", 55.0
    return "flat", 45.0


# composite weights (single source of truth for screen_symbol and needs_option_chain)
W_MOM, W_VOL, W_OI, W_IV = 0.40, 0.20, 0.30, 0.10
OI_SCORE_UNKNOWN, OI_SCORE_MAX = 50.0, 80.0   # no option chain -> 50; the best case (long/short build-up) -> 80


def needs_option_chain(snapshot: UnderlyingSnapshot, margin: float = 1.0) -> bool:
    """Two-stage scan, stage 1: can this symbol still reach WATCHLIST_MIN_SCORE once its option-chain (OI) part is known?
    `snapshot` has no option chain, so its score uses a neutral OI part (50). The real OI part is at most 80, which adds at most
    W_OI * (80 - 50) = 9 points. So a symbol whose chain-less score is below the floor minus that uplift can never reach the
    watch/alert band, and downloading its option quotes would be wasted. A neutral (no clear trend) symbol never alerts either."""
    r = screen_symbol(snapshot)
    if r.bias == "NEUTRAL":
        return False
    floor = min(config.WATCHLIST_MIN_SCORE, config.MIN_SIGNAL_SCORE) - W_OI * (OI_SCORE_MAX - OI_SCORE_UNKNOWN) - margin
    return r.score >= floor


def screen_symbol(snapshot: UnderlyingSnapshot) -> ScreenResult:
    closes = pd.Series(pd.to_numeric(
        snapshot.history.get("CH_CLOSING_PRICE", pd.Series(dtype=float)), errors="coerce"
    )).dropna()

    mom_score, bias, r = _momentum_score(closes)
    vol_score, vol_ratio = _volume_score(snapshot.day_volume, snapshot.avg_20d_volume)
    oi_sig, oi_score = _oi_signal(snapshot, bias)

    ivs = pd.Series([q.iv for q in snapshot.option_chain if q.iv])
    atm_iv = ivs.mean() if not ivs.empty else 0.0
    iv_pct = iv_percentile(atm_iv, ivs) if not ivs.empty else 50.0
    iv_score = 70.0 if 30 <= iv_pct <= 85 else 45.0

    composite = (
        W_MOM * mom_score +
        W_VOL * vol_score +
        W_OI * oi_score +
        W_IV * iv_score
    )

    trigger_reasons = []
    if bias in ("BULLISH", "BEARISH"):
        trigger_reasons = trig.collect_triggers(snapshot.history, bullish=(bias == "BULLISH"))
        if vol_ratio >= config.MIN_VOLUME_SPIKE_RATIO:
            trigger_reasons.append(f"volume {vol_ratio:.1f}x the 20-day average")
        if oi_sig in ("long_buildup", "short_buildup"):
            trigger_reasons.append(f"options OI shows {oi_sig.replace('_', ' ')}")

    notes = []
    if vol_ratio < config.MIN_VOLUME_SPIKE_RATIO:
        notes.append("volume unremarkable")
    if oi_sig in ("flat", "unknown"):
        notes.append("no clear OI conviction")
    if bias == "NEUTRAL":
        notes.append("no clean trend")

    return ScreenResult(
        symbol=snapshot.symbol,
        bias=bias,
        score=round(composite, 1),
        spot=snapshot.spot,
        rsi_value=round(r, 1),
        volume_ratio=round(vol_ratio, 2),
        oi_signal=oi_sig,
        iv_pct=round(iv_pct, 1),
        trigger_reasons=trigger_reasons,
        notes="; ".join(notes) if notes else "clean setup",
    )


def rank_universe(snapshots: list[UnderlyingSnapshot], top_n: int = None) -> list[ScreenResult]:
    top_n = top_n or config.TOP_N_SIGNALS_PER_DAY
    results = [screen_symbol(s) for s in snapshots]
    # Only keep directional (non-neutral) ideas that clear the quality floor -
    # deliberately does NOT force-fill up to top_n with mediocre setups just
    # to have something to show. Some scans will legitimately return nothing.
    directional = [r for r in results
                   if r.bias != "NEUTRAL" and r.score >= config.MIN_SIGNAL_SCORE]
    directional.sort(key=lambda r: r.score, reverse=True)
    return directional[:top_n]


def watchlist_candidates(snapshots: list[UnderlyingSnapshot]) -> list[ScreenResult]:
    """Symbols with a real directional bias that haven't (yet) cleared
    MIN_SIGNAL_SCORE - a lighter-weight heads-up tier, meant to fire a scan
    cycle or two before (if ever) a symbol crosses into a full buy signal.
    Not a promise that a signal is coming, just an early "worth watching"."""
    results = [screen_symbol(s) for s in snapshots]
    watch = [r for r in results
             if r.bias != "NEUTRAL" and config.WATCHLIST_MIN_SCORE <= r.score < config.ALERT_SCORE]
    watch.sort(key=lambda r: r.score, reverse=True)
    return watch
