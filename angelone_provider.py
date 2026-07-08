"""
angelone_provider.py
---------------------
Live data provider backed by Angel One SmartAPI, as a drop-in replacement
for the NSE-scraping provider that kept getting blocked. Implements the
same interface data_fetcher.py's other providers do
(`get_underlying_snapshot(symbol, is_index) -> UnderlyingSnapshot`), so
nothing in screener.py / strategy.py / paper_trader.py needs to change.

CREDENTIALS: read only from environment variables, never from a config
file or from this codebase, since these four values together can log into
a real trading account:
    ANGELONE_API_KEY
    ANGELONE_CLIENT_CODE
    ANGELONE_PIN
    ANGELONE_TOTP_SECRET
See README.md "Angel One SmartAPI setup" for how to get these.

VERIFIED (2026-07-06) end-to-end against a live account, both stocks and
indices, via diagnose_angelone.py AND a full `main.py --mode watchlist
--broker angelone` run:
  - generateSession / ltpData / getCandleData / optionGreek / getMarketData
    all match the shapes used below.
  - Stocks: equity LTP, historical candles, option instrument lookup (fixed
    two real bugs along the way - an NFO-vs-BFO exchange mixup, and a
    naive string sort on expiry dates that picked the wrong expiry),
    option quotes via getMarketData (ltpData alone does NOT carry OI for
    options), and optionGreek (IV) all confirmed working for RELIANCE and
    produced real signals for HDFCBANK/ICICIBANK/etc.
  - Indices: NIFTY/BANKNIFTY/FINNIFTY spot + option chain lookup confirmed
    working too (fixed a backwards name/symbol field assumption - the
    instrument master's 'name' field IS the plain symbol, e.g. name=
    "NIFTY", not a pretty label). NIFTY correctly picked a weekly expiry
    while stocks picked monthly, confirming the current NSE rules (stock
    options are monthly-only; index options still get weekly expiries).
  - Known gap: config.py's STOCK_UNIVERSE has one stale symbol
    (TATAMOTORS, post-demerger - see README) that gets skipped harmlessly.
"""

import os
import json
import time
import datetime as dt

import pandas as pd
import requests

from data_fetcher import UnderlyingSnapshot, OptionQuote

INSTRUMENT_MASTER_URL = (
    "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
)
INSTRUMENT_CACHE_FILE = "angelone_instruments_cache.json"
INSTRUMENT_CACHE_MAX_AGE_HOURS = 20

# VERIFIED via diagnose_angelone.py: index spot instruments live under
# exch_seg "NSE", instrumenttype "AMXIDX", and their 'name' field is simply
# the plain symbol itself (name="NIFTY", name="BANKNIFTY") - the human
# readable label ("Nifty 50", "Nifty Bank") is in the 'symbol' field
# instead, which is the opposite of what was first assumed. No name-mapping
# needed - matching directly on symbol.upper() against 'name' works.
INDEX_INSTRUMENT_TYPE = "AMXIDX"

# SmartAPI's data endpoints (especially historical candles) rate-limit much
# more aggressively than the documented order-placement limits. Firing calls
# back-to-back gets you errors like "Access denied because of exceeding
# access rate" OR "Too many requests" (errorcode AB1021) - both are the same
# underlying problem, not an error in your setup, just needs throttling.
MIN_SECONDS_BETWEEN_CALLS = 1.0
RATE_LIMIT_RETRY_DELAYS = [2.0, 5.0, 10.0]   # backoff schedule on a rate-limit hit

# Substrings (checked lowercase) that indicate a rate-limit response rather
# than a real error - seen in the wild from Angel One so far:
#   "Access denied because of exceeding access rate"
#   "Too many requests" (errorcode AB1021)
_RATE_LIMIT_MARKERS = ("exceeding access rate", "too many requests", "ab1021", "429")

_last_call_at = [0.0]  # mutable single-item list so the module-level throttle persists


def _throttled_call(fn, *args, **kwargs):
    """Wraps any SmartApi method call with a minimum gap since the last call,
    plus retry-with-backoff specifically for rate-limit errors."""
    elapsed = time.time() - _last_call_at[0]
    if elapsed < MIN_SECONDS_BETWEEN_CALLS:
        time.sleep(MIN_SECONDS_BETWEEN_CALLS - elapsed)

    last_exc = None
    for attempt, delay in enumerate([0] + RATE_LIMIT_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            result = fn(*args, **kwargs)
            _last_call_at[0] = time.time()
            return result
        except Exception as exc:
            last_exc = exc
            _last_call_at[0] = time.time()
            if not any(marker in str(exc).lower() for marker in _RATE_LIMIT_MARKERS):
                raise  # not a rate-limit issue - don't waste time retrying
    raise last_exc


def _require_credentials() -> dict:
    creds = {
        "api_key": os.environ.get("ANGELONE_API_KEY"),
        "client_code": os.environ.get("ANGELONE_CLIENT_CODE"),
        "pin": os.environ.get("ANGELONE_PIN"),
        "totp_secret": os.environ.get("ANGELONE_TOTP_SECRET"),
    }
    missing = [k for k, v in creds.items() if not v]
    if missing:
        raise RuntimeError(
            f"Missing Angel One credentials in environment: {missing}. "
            f"Set ANGELONE_API_KEY / ANGELONE_CLIENT_CODE / ANGELONE_PIN / "
            f"ANGELONE_TOTP_SECRET (see README 'Angel One SmartAPI setup')."
        )
    return creds


class AngelOneDataProvider:
    def __init__(self):
        try:
            from SmartApi import SmartConnect
            import pyotp
        except ImportError as exc:
            raise RuntimeError(
                "Run `pip3 install smartapi-python pyotp` first."
            ) from exc

        creds = _require_credentials()
        self.smart_api = SmartConnect(creds["api_key"])
        totp = pyotp.TOTP(creds["totp_secret"]).now()

        session = self.smart_api.generateSession(creds["client_code"], creds["pin"], totp)
        if not session or not session.get("status"):
            raise RuntimeError(f"Angel One login failed: {session}")

        self.instruments = self._load_instrument_master()
        self._history_cache = {}   # {(exchange, token): (fetched_at, DataFrame)}
        self._history_cache_max_age = 1800  # 30 min - daily candles barely change
                                             # intraday except today's row, so a
                                             # continuous monitor loop shouldn't
                                             # re-fetch this every single cycle
        self._iv_cache = {}        # {(symbol, expiry): (fetched_at, iv_map)}

    # -- instrument master -------------------------------------------------
    def _load_instrument_master(self) -> list:
        if (os.path.exists(INSTRUMENT_CACHE_FILE)
                and time.time() - os.path.getmtime(INSTRUMENT_CACHE_FILE)
                < INSTRUMENT_CACHE_MAX_AGE_HOURS * 3600):
            with open(INSTRUMENT_CACHE_FILE) as f:
                return json.load(f)

        resp = requests.get(INSTRUMENT_MASTER_URL, timeout=30)
        resp.raise_for_status()
        instruments = resp.json()
        with open(INSTRUMENT_CACHE_FILE, "w") as f:
            json.dump(instruments, f)
        return instruments

    def _find_equity_or_index_token(self, symbol: str, is_index: bool) -> dict:
        if is_index:
            matches = [i for i in self.instruments
                       if i.get("exch_seg") == "NSE" and i.get("name", "").upper() == symbol.upper()
                       and i.get("instrumenttype", "") == INDEX_INSTRUMENT_TYPE]
        else:
            matches = [i for i in self.instruments
                       if i.get("exch_seg") == "NSE" and i.get("symbol", "") == f"{symbol}-EQ"]
        if not matches:
            raise ValueError(f"No instrument token found for {symbol} (is_index={is_index}) "
                              f"- run diagnose_angelone.py to inspect the instrument master.")
        return matches[0]

    def _find_option_instruments(self, symbol: str, is_index: bool) -> list:
        # IMPORTANT: Angel One's instrument master lists the same underlying's
        # options on BOTH NSE (exch_seg "NFO") and BSE (exch_seg "BFO"). We
        # only want NFO - mixing them causes "Symbol token not found in scrip
        # master cache for the given exchange" errors on the LTP call.
        prefix = symbol.upper()
        opt_type = "OPTIDX" if is_index else "OPTSTK"
        matches = [i for i in self.instruments
                   if i.get("instrumenttype") == opt_type
                   and i.get("name", "").upper() == prefix
                   and i.get("exch_seg") == "NFO"]
        return matches

    @staticmethod
    def _parse_expiry(expiry: str) -> dt.date:
        """Angel One expiry strings look like '28JUL2026' - NOT sortable as
        plain text across month boundaries (e.g. '24SEP2026' < '28JUL2026'
        alphabetically despite being later). Always sort by this parsed date."""
        return dt.datetime.strptime(expiry, "%d%b%Y").date()

    # -- market data ---------------------------------------------------
    def _get_ltp(self, exchange: str, tradingsymbol: str, token: str) -> float:
        data = _throttled_call(self.smart_api.ltpData, exchange, tradingsymbol, token)
        return float(data["data"]["ltp"])

    def _get_historical_daily(self, exchange: str, token: str, days: int = 60) -> pd.DataFrame:
        cache_key = (exchange, token)
        cached = self._history_cache.get(cache_key)
        if cached and time.time() - cached[0] < self._history_cache_max_age:
            return cached[1]

        to_date = dt.date.today()
        from_date = to_date - dt.timedelta(days=days * 2)
        params = {
            "exchange": exchange,
            "symboltoken": token,
            "interval": "ONE_DAY",
            "fromdate": from_date.strftime("%Y-%m-%d 09:00"),
            "todate": to_date.strftime("%Y-%m-%d 15:30"),
        }
        resp = _throttled_call(self.smart_api.getCandleData, params)
        candles = resp.get("data", []) if resp else []
        rows = []
        for c in candles:
            # documented shape: [timestamp, open, high, low, close, volume]
            rows.append({
                "CH_TIMESTAMP": str(c[0])[:10],
                "CH_OPENING_PRICE": c[1],
                "CH_TRADE_HIGH_PRICE": c[2],
                "CH_TRADE_LOW_PRICE": c[3],
                "CH_CLOSING_PRICE": c[4],
                "CH_PREVIOUS_CLS_PRICE": rows[-1]["CH_CLOSING_PRICE"] if rows else c[4],
                "CH_TOT_TRADED_QTY": c[5],
            })
        df = pd.DataFrame(rows)
        self._history_cache[cache_key] = (time.time(), df)
        return df

    def _load_iv_map(self, symbol: str, expiry: str) -> dict:
        """Best-effort IV lookup via the optionGreek endpoint, keyed by
        (strike, option_type). Returns {} on any failure - IV just scores
        neutral (50th percentile) if unavailable rather than breaking the run.
        Cached briefly (5 min) - IV doesn't need refreshing every monitor cycle."""
        cache_key = (symbol, expiry)
        cached = self._iv_cache.get(cache_key)
        if cached and time.time() - cached[0] < 300:
            return cached[1]
        try:
            resp = _throttled_call(self.smart_api.optionGreek, {"name": symbol, "expirydate": expiry})
            rows = resp.get("data", []) if resp else []
            iv_map = {}
            for row in rows:
                strike = float(row.get("strikePrice", 0))
                iv_map[(strike, "CE")] = float(row.get("impliedVolatility", 0) or 0)
                # some SmartAPI responses split CE/PE into separate rows instead of
                # one combined row - handle both shapes defensively.
                if "optionType" in row:
                    iv_map[(strike, row["optionType"])] = float(row.get("impliedVolatility", 0) or 0)
            self._iv_cache[cache_key] = (time.time(), iv_map)
            return iv_map
        except Exception:
            return {}

    def _oi_change_pct(self, token: str, current_oi: int, cache_file: str = "angelone_oi_history.json") -> float:
        """SmartAPI's LTP call only gives today's current OI, not the change -
        so we track our own snapshots locally and diff against the earliest
        reading of the day (a simple stand-in for 'OI buildup since morning')."""
        today = dt.date.today().isoformat()
        history = {}
        if os.path.exists(cache_file):
            try:
                with open(cache_file) as f:
                    history = json.load(f)
            except Exception:
                history = {}

        day_entry = history.get(today, {})
        first_oi = day_entry.get(token)
        if first_oi is None:
            day_entry[token] = current_oi
            first_oi = current_oi
        history[today] = day_entry
        # keep only today + yesterday to stop the file growing forever
        history = {k: v for k, v in sorted(history.items())[-2:]}
        with open(cache_file, "w") as f:
            json.dump(history, f)

        if not first_oi:
            return 0.0
        return round((current_oi - first_oi) / first_oi * 100, 2)

    STRIKES_EACH_SIDE = 3     # ATM +/- this many strikes, per option type
    MAX_TOKENS_PER_REQUEST = 40   # Angel One's getMarketData rejects overly large batches

    def _get_option_chain(self, symbol: str, is_index: bool, spot: float) -> list:
        instruments = self._find_option_instruments(symbol, is_index)
        if not instruments:
            return []
        expiries = sorted(set(i["expiry"] for i in instruments if i.get("expiry")),
                          key=self._parse_expiry)
        if not expiries:
            return []
        nearest_expiry = expiries[0]
        this_expiry = [i for i in instruments if i["expiry"] == nearest_expiry]

        # Fixed-count window around ATM instead of a %-band - a %-band scales
        # unpredictably with each stock's strike spacing and is what blew past
        # Angel One's per-call token limit on a stock with fine strike steps.
        near_atm = []
        for opt_type in ("CE", "PE"):
            side = [i for i in this_expiry if i["symbol"].endswith(opt_type)]
            side.sort(key=lambda i: abs(float(i["strike"]) / 100 - spot))
            near_atm.extend(side[: self.STRIKES_EACH_SIDE * 2 + 1])

        iv_map = self._load_iv_map(symbol, nearest_expiry)

        # VERIFIED via diagnose_angelone.py: ltpData does NOT include OI/volume
        # for options - only getMarketData("FULL", ...) does, under
        # data.fetched[i].opnInterest / .tradeVolume (confirmed field names).
        # Batched calls, chunked to stay under Angel One's per-request token cap.
        by_token = {inst["token"]: inst for inst in near_atm}
        quotes = []
        if not by_token:
            return quotes

        tokens = list(by_token.keys())
        fetched = []
        for i in range(0, len(tokens), self.MAX_TOKENS_PER_REQUEST):
            chunk = tokens[i:i + self.MAX_TOKENS_PER_REQUEST]
            try:
                resp = _throttled_call(self.smart_api.getMarketData, "FULL", {"NFO": chunk})
                fetched.extend(resp.get("data", {}).get("fetched", []) if resp else [])
            except Exception:
                continue

        for row in fetched:
            inst = by_token.get(str(row.get("symbolToken")))
            if not inst:
                continue
            strike = float(inst["strike"]) / 100  # Angel One stores strike * 100
            option_type = "CE" if inst["symbol"].endswith("CE") else "PE"
            current_oi = int(row.get("opnInterest", 0) or 0)
            quotes.append(OptionQuote(
                symbol=symbol,
                expiry=nearest_expiry,
                strike=strike,
                option_type=option_type,
                ltp=float(row.get("ltp", 0)),
                oi=current_oi,
                oi_change_pct=self._oi_change_pct(inst["token"], current_oi),
                iv=iv_map.get((strike, option_type), 0.0),
                volume=int(row.get("tradeVolume", 0) or 0),
            ))
        return quotes

    def get_underlying_snapshot(self, symbol: str, is_index: bool = False) -> UnderlyingSnapshot:
        inst = self._find_equity_or_index_token(symbol, is_index)
        exchange = "NSE"
        spot = self._get_ltp(exchange, inst["symbol"], inst["token"])
        history = self._get_historical_daily(exchange, inst["token"])
        option_chain = self._get_option_chain(symbol, is_index, spot)

        return UnderlyingSnapshot(
            symbol=symbol,
            spot=spot,
            prev_close=float(history["CH_PREVIOUS_CLS_PRICE"].iloc[-1]) if not history.empty else spot,
            day_volume=int(history["CH_TOT_TRADED_QTY"].iloc[-1]) if not history.empty else 0,
            avg_20d_volume=float(history["CH_TOT_TRADED_QTY"].tail(20).mean()) if not history.empty else 0.0,
            history=history,
            option_chain=option_chain,
        )
