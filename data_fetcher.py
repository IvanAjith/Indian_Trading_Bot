"""
data_fetcher.py
---------------
Pulls the data the rest of the bot needs:
  - live option chain (LTP, OI, IV, volume) for an index/stock
  - historical daily OHLC for the underlying (for technical indicators)

Two sources are wired up:
  1. NSE's public website API (free, no login) - good enough for building
     and paper trading, but it's unofficial, rate-limited, and NSE
     occasionally changes its anti-bot checks.
  2. A "mock" data provider that generates realistic-looking synthetic data,
     used automatically when NSE isn't reachable (e.g. in this sandbox) and
     useful for testing the screener/strategy/paper-trader logic offline.

When you're ready for a proper live feed, swap `NSEDataProvider` for a
broker SDK call (Upstox / Angel One SmartAPI / Zerodha Kite) - the rest of
the bot only depends on the plain dict/DataFrame shapes returned here, not
on NSE specifically. See README.md "Swapping in a broker feed".
"""

import time
import random
import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

import requests
import pandas as pd


# ---------------------------------------------------------------------------
# Shared data shape used by every provider
# ---------------------------------------------------------------------------
@dataclass
class OptionQuote:
    symbol: str
    expiry: str
    strike: float
    option_type: str   # "CE" or "PE"
    ltp: float
    oi: int
    oi_change_pct: float
    iv: float
    volume: int
    lot_size: int = 1   # units per lot (e.g. NIFTY 75). P&L = price move x lots x lot_size


@dataclass
class UnderlyingSnapshot:
    symbol: str
    spot: float
    prev_close: float
    day_volume: int
    avg_20d_volume: float
    history: pd.DataFrame = field(default_factory=pd.DataFrame)  # OHLC, daily
    option_chain: list = field(default_factory=list)  # list[OptionQuote]


# ---------------------------------------------------------------------------
# Real provider: NSE public API
# ---------------------------------------------------------------------------
class NSEDataProvider:
    """Unofficial NSE website API. Free, no key needed, but fragile.

    Run this from your own machine/server (not a locked-down sandbox) -
    NSE blocks obvious datacenter IPs and requires realistic browser headers.
    """

    BASE = "https://www.nseindia.com"

    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "X-Requested-With": "XMLHttpRequest",
        })
        self._warmed = False

    def _warm_up(self, referer_path: str = "/option-chain"):
        # NSE only hands over usable cookies after you've "visited" a real
        # page in a browser-like way, and it wants a matching Referer header
        # on the subsequent /api/ call. Re-warming per call type (indices vs
        # equities) fixes most of the sporadic 401/403/404 responses people
        # hit with the plain homepage-only warm-up.
        self.session.headers["Referer"] = f"{self.BASE}{referer_path}"
        self.session.get(f"{self.BASE}{referer_path}", timeout=self.timeout)
        time.sleep(1)
        self._warmed = True

    def get_option_chain(self, symbol: str, is_index: bool) -> dict:
        path = "option-chain-indices" if is_index else "option-chain-equities"
        referer = f"/option-chain?symbol={symbol}" if is_index else f"/get-quote/equity/{symbol}"
        self._warm_up(referer)
        url = f"{self.BASE}/api/{path}?symbol={symbol}"

        resp = self.session.get(url, timeout=self.timeout)
        if resp.status_code != 200 or "records" not in resp.text:
            # One retry with a fresh session -- NSE cookies/anti-bot tokens
            # sometimes only work for a single request.
            time.sleep(1.5)
            self._warmed = False
            self._warm_up(referer)
            resp = self.session.get(url, timeout=self.timeout)

        resp.raise_for_status()
        payload = resp.json()
        if "records" not in payload:
            raise ValueError(
                f"NSE responded 200 but without the expected 'records' field "
                f"for {symbol} - likely an anti-bot/challenge page rather "
                f"than real data. Run diagnose_nse.py to see the raw response."
            )
        return payload

    def get_historical_daily(self, symbol: str, days: int = 60) -> pd.DataFrame:
        # NSE's historical endpoints move around often; if this breaks,
        # swap in your broker's historical-candle API instead (all of Upstox/
        # Angel One/Zerodha expose one) - it plugs into the same DataFrame
        # shape: columns = [date, open, high, low, close, volume].
        self._warm_up(f"/get-quote/equity/{symbol}")
        to_date = dt.date.today()
        from_date = to_date - dt.timedelta(days=days * 2)  # buffer for holidays
        url = (
            f"{self.BASE}/api/historical/cm/equity?symbol={symbol}"
            f"&series=[%22EQ%22]&from={from_date:%d-%m-%Y}&to={to_date:%d-%m-%Y}"
        )
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        df = pd.DataFrame(data)
        return df

    def get_underlying_snapshot(self, symbol: str, is_index: bool = False) -> UnderlyingSnapshot:
        chain_json = self.get_option_chain(symbol, is_index)
        records = chain_json["records"]
        spot = records["underlyingValue"]
        quotes = []
        for row in records["data"]:
            for side, key in (("CE", "CE"), ("PE", "PE")):
                leg = row.get(key)
                if not leg:
                    continue
                quotes.append(OptionQuote(
                    symbol=symbol,
                    expiry=row.get("expiryDate", ""),
                    strike=row.get("strikePrice", 0.0),
                    option_type=side,
                    ltp=leg.get("lastPrice", 0.0),
                    oi=leg.get("openInterest", 0),
                    oi_change_pct=leg.get("pchangeinOpenInterest", 0.0),
                    iv=leg.get("impliedVolatility", 0.0),
                    volume=leg.get("totalTradedVolume", 0),
                    lot_size=1,   # NSE site feed does not give lot size; use --broker angelone for real sizes
                ))
        hist = self.get_historical_daily(symbol)
        return UnderlyingSnapshot(
            symbol=symbol,
            spot=spot,
            prev_close=hist["CH_PREVIOUS_CLS_PRICE"].iloc[-1] if not hist.empty else spot,
            day_volume=int(hist["CH_TOT_TRADED_QTY"].iloc[-1]) if not hist.empty else 0,
            avg_20d_volume=float(hist["CH_TOT_TRADED_QTY"].tail(20).mean()) if not hist.empty else 0.0,
            history=hist,
            option_chain=quotes,
        )


# ---------------------------------------------------------------------------
# Mock provider: deterministic-ish synthetic data for offline testing
# ---------------------------------------------------------------------------
class MockDataProvider:
    """Generates plausible fake data so you can test screener/strategy/paper
    trading logic without network access or market hours. Not for real
    decisions - swap to NSEDataProvider or a broker feed for that.
    """

    def __init__(self, seed: Optional[int] = 42):
        self.rng = random.Random(seed)

    def _fake_history(self, base_price: float, days: int = 60) -> pd.DataFrame:
        rows = []
        price = base_price * 0.9
        today = dt.date.today()
        for i in range(days, 0, -1):
            drift = self.rng.uniform(-0.015, 0.018)
            price = max(1.0, price * (1 + drift))
            vol = int(self.rng.uniform(5e5, 3e6))
            d = today - dt.timedelta(days=i)
            rows.append({
                "CH_TIMESTAMP": d.isoformat(),
                "CH_OPENING_PRICE": price * 0.995,
                "CH_TRADE_HIGH_PRICE": price * 1.01,
                "CH_TRADE_LOW_PRICE": price * 0.985,
                "CH_CLOSING_PRICE": price,
                "CH_PREVIOUS_CLS_PRICE": price * (1 - drift),
                "CH_TOT_TRADED_QTY": vol,
            })
        return pd.DataFrame(rows)

    def get_underlying_snapshot(self, symbol: str, is_index: bool = False) -> UnderlyingSnapshot:
        base_price = self.rng.uniform(200, 3000) if not is_index else self.rng.uniform(19000, 52000)
        hist = self._fake_history(base_price)
        spot = float(hist["CH_CLOSING_PRICE"].iloc[-1])
        strike_step = 50 if not is_index else (100 if symbol != "BANKNIFTY" else 100)
        atm = round(spot / strike_step) * strike_step
        expiry = (dt.date.today() + dt.timedelta(days=(3 - dt.date.today().weekday()) % 7 or 7)).isoformat()

        quotes = []
        for offset in range(-3, 4):
            strike = atm + offset * strike_step
            for side in ("CE", "PE"):
                intrinsic = max(0.0, (spot - strike) if side == "CE" else (strike - spot))
                time_value = self.rng.uniform(5, 40)
                quotes.append(OptionQuote(
                    symbol=symbol,
                    expiry=expiry,
                    strike=strike,
                    option_type=side,
                    ltp=round(intrinsic + time_value, 2),
                    oi=int(self.rng.uniform(5e4, 5e5)),
                    oi_change_pct=round(self.rng.uniform(-20, 30), 2),
                    iv=round(self.rng.uniform(14, 45), 2),
                    volume=int(self.rng.uniform(1e4, 2e5)),
                    lot_size=75 if is_index else max(5, int(round(600000 / spot / 5)) * 5),
                ))

        return UnderlyingSnapshot(
            symbol=symbol,
            spot=spot,
            prev_close=float(hist["CH_PREVIOUS_CLS_PRICE"].iloc[-1]),
            day_volume=int(hist["CH_TOT_TRADED_QTY"].iloc[-1]),
            avg_20d_volume=float(hist["CH_TOT_TRADED_QTY"].tail(20).mean()),
            history=hist,
            option_chain=quotes,
        )


def get_provider(prefer_live: bool = True, broker: str = "nse", strict: bool = False):
    """Returns a live data provider, or falls back to mock.

    broker="nse"      -> unofficial NSE website scrape (free, but frequently
                          blocked by anti-bot measures - see README)
    broker="angelone" -> Angel One SmartAPI (requires ANGELONE_* env vars -
                          see README "Angel One SmartAPI setup")

    strict=True -> never fall back to fake data: raise instead, so an
    unattended run (GitHub Actions) fails loudly rather than quietly running
    on synthetic prices.

    Call this once and reuse the object (keeps sessions/logins warm).
    """
    if prefer_live and broker == "angelone":
        try:
            from angelone_provider import AngelOneDataProvider
            return AngelOneDataProvider()
        except Exception as exc:
            if strict:
                raise
            print(f"[data_fetcher] Angel One login/setup failed ({exc}); falling back to MockDataProvider.")
            return MockDataProvider()

    if prefer_live:
        try:
            provider = NSEDataProvider()
            provider._warm_up()
            return provider
        except Exception as exc:
            if strict:
                raise
            print(f"[data_fetcher] NSE unreachable ({exc}); falling back to MockDataProvider.")
    return MockDataProvider()
