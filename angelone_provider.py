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
import threading
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

from data_fetcher import UnderlyingSnapshot, OptionQuote
import config
from indicators import ema

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

# Diagnostics shown in the end-of-day summary: how many API calls were made and how many were
# slowed by Angel One's rate limit (each such hit costs a 2/5/10 s backoff).
STATS = {"calls": 0, "rate_limit_retries": 0}

# Historical candles may be fetched a few at a time (Angel One allows ~3/second): CANDLE_WORKERS
# requests in flight, with at least CANDLE_MIN_GAP seconds between request starts.
CANDLE_WORKERS = 3
CANDLE_MIN_GAP = 0.4
_candle_gate = threading.Lock()
_last_candle_at = [0.0]


_call_lock = threading.RLock()   # the scan thread and the fast open-trade price check share this


def _throttled_call(fn, *args, **kwargs):
    """Wraps any SmartApi method call with a minimum gap since the last call,
    plus retry-with-backoff specifically for rate-limit errors. One call at a time."""
    with _call_lock:
        return _throttled_call_locked(fn, *args, **kwargs)


def _throttled_call_locked(fn, *args, **kwargs):
    elapsed = time.time() - _last_call_at[0]
    if elapsed < MIN_SECONDS_BETWEEN_CALLS:
        time.sleep(MIN_SECONDS_BETWEEN_CALLS - elapsed)

    last_exc = None
    for attempt, delay in enumerate([0] + RATE_LIMIT_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            STATS["calls"] += 1
            result = fn(*args, **kwargs)
            _last_call_at[0] = time.time()
            return result
        except Exception as exc:
            last_exc = exc
            _last_call_at[0] = time.time()
            if not any(marker in str(exc).lower() for marker in _RATE_LIMIT_MARKERS):
                raise  # not a rate-limit issue - don't waste time retrying
            STATS["rate_limit_retries"] += 1
    raise last_exc


def _throttled_candle_call(fn, *args, **kwargs):
    """Like _throttled_call but safe to use from several threads at once (candle downloads only)."""
    last_exc = None
    for attempt, delay in enumerate([0] + RATE_LIMIT_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        with _candle_gate:   # only spaces out request STARTS; the calls themselves overlap
            wait = CANDLE_MIN_GAP - (time.time() - _last_candle_at[0])
            if wait > 0:
                time.sleep(wait)
            _last_candle_at[0] = time.time()
        try:
            STATS["calls"] += 1
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if not any(marker in str(exc).lower() for marker in _RATE_LIMIT_MARKERS):
                raise
            STATS["rate_limit_retries"] += 1
    raise last_exc


def _require_credentials() -> dict:
    # .strip(): secrets pasted into GitHub/env files often carry a hidden
    # trailing newline or space, which makes login fail with a confusing
    # "invalid header value" error. TOTP secret also has spaces removed.
    def _clean(name):
        return (os.environ.get(name) or "").strip()
    creds = {
        "api_key": _clean("ANGELONE_API_KEY"),
        "client_code": _clean("ANGELONE_CLIENT_CODE"),
        "pin": _clean("ANGELONE_PIN"),
        "totp_secret": _clean("ANGELONE_TOTP_SECRET").replace(" ", ""),
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
    STRIKES_EACH_SIDE = 3     # ATM +/- this many strikes, per option type
    MAX_TOKENS_PER_REQUEST = 40   # Angel One's getMarketData rejects overly large batches

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
        self._build_indexes()
        self._history_cache = {}   # {(exchange, token): (fetched_at, DataFrame)}
        self._history_cache_max_age = 8 * 3600   # daily candles barely change intraday (whole session); the live
                                                 # price is patched into today's row on every scan
                                                 # (see _with_live_today), so re-fetching every
                                                 # 30 min (a ~100 s stall for ~80 symbols) isn't needed
        self._iv_cache = {}        # {(symbol, expiry): (fetched_at, iv_map)}
        self._universe_cache = None   # (date, [(symbol, is_index), ...])
        self.watch_contracts = set()   # {(symbol, strike, option_type, expiry)} of trades we hold
        self._oi_state = None      # lazily loaded {date: {token: first_oi}}
        self._oi_dirty = False

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

    def _build_indexes(self):
        """One pass over the (huge) instrument master so lookups are dict hits
        instead of re-scanning ~100k+ rows for every symbol on every scan."""
        self._eq_index = {}    # "RELIANCE" -> NSE cash instrument
        self._idx_index = {}   # "NIFTY"    -> NSE index instrument
        self._opt_index = {}   # (OPTSTK|OPTIDX, "RELIANCE") -> [NFO option instruments]
        for i in self.instruments:
            seg = i.get("exch_seg")
            if seg == "NSE":
                if i.get("instrumenttype", "") == INDEX_INSTRUMENT_TYPE:
                    self._idx_index.setdefault(i.get("name", "").upper(), i)
                sym = i.get("symbol", "")
                if sym.endswith("-EQ"):
                    self._eq_index.setdefault(sym[:-3], i)
            elif seg == "NFO" and i.get("instrumenttype") in ("OPTIDX", "OPTSTK"):
                self._opt_index.setdefault((i["instrumenttype"], i.get("name", "").upper()), []).append(i)

    def _find_equity_or_index_token(self, symbol: str, is_index: bool) -> dict:
        inst = self._idx_index.get(symbol.upper()) if is_index else self._eq_index.get(symbol)
        if not inst:
            raise ValueError(f"No instrument token found for {symbol} (is_index={is_index}) "
                              f"- run diagnose_angelone.py to inspect the instrument master.")
        return inst

    def _find_option_instruments(self, symbol: str, is_index: bool) -> list:
        # IMPORTANT: Angel One's instrument master lists the same underlying's
        # options on BOTH NSE (exch_seg "NFO") and BSE (exch_seg "BFO"). We
        # only want NFO - mixing them causes "Symbol token not found in scrip
        # master cache for the given exchange" errors on the LTP call.
        opt_type = "OPTIDX" if is_index else "OPTSTK"
        return self._opt_index.get((opt_type, symbol.upper()), [])

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

    def _market_data(self, exchange: str, tokens: list) -> dict:
        """Batched getMarketData("FULL") -> {token(str): row}. One API call per
        MAX_TOKENS_PER_REQUEST tokens instead of one call per symbol - this is
        what makes scanning a wide universe fast. A failed batch is logged and
        skipped (callers fall back / skip the affected symbols)."""
        out = {}
        for i in range(0, len(tokens), self.MAX_TOKENS_PER_REQUEST):
            chunk = tokens[i:i + self.MAX_TOKENS_PER_REQUEST]
            try:
                resp = _throttled_call(self.smart_api.getMarketData, "FULL", {exchange: chunk})
            except Exception as exc:
                print(f"[angelone] getMarketData {exchange} batch of {len(chunk)} failed: {exc}")
                continue
            for row in (resp.get("data", {}).get("fetched", []) if resp else []):
                out[str(row.get("symbolToken"))] = row
        return out

    def quick_prices(self, contracts) -> dict:
        """Fast price check for contracts we hold: {(symbol, strike, option_type, expiry): ltp}.
        One batched quote call, no history and no option chain, so it takes about a second. Used
        between full scans so targets/stoploss are noticed within seconds, not scan-minutes."""
        cache = self.__dict__.setdefault("_quick_inst", {})
        wanted = {}
        for key in contracts:
            if key not in cache:
                hs, hk, ht, he = key
                cache[key] = self._find_contract(hs, hk, ht, he)
            if cache[key]:
                wanted[key] = str(cache[key]["token"])
        if not wanted:
            return {}
        rows = self._market_data("NFO", list(dict.fromkeys(wanted.values())))
        out = {}
        for key, token in wanted.items():
            row = rows.get(token)
            if row and row.get("ltp"):
                out[key] = float(row["ltp"])
        return out

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
        resp = _throttled_candle_call(self.smart_api.getCandleData, params)
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

    @staticmethod
    def _with_live_today(history: pd.DataFrame, spot: float) -> pd.DataFrame:
        """Make sure the last row is TODAY's, carrying the live price, so RSI/EMA/breakout checks see the current price even
        though the daily history is downloaded only once. If the history was downloaded before today's candle existed (e.g.
        pre-loaded before the open), a row for today is added: open/high/low/close = the live price, previous close = the last
        close. Volume is not taken from this row (the screener uses the live traded volume)."""
        if history is None or history.empty or "CH_TIMESTAMP" not in history:
            return history
        today = dt.date.today().isoformat()
        last = str(history["CH_TIMESTAMP"].iloc[-1])
        if last > today:
            return history
        if last < today:
            row = {c: None for c in history.columns}
            row.update({
                "CH_TIMESTAMP": today,
                "CH_OPENING_PRICE": float(spot), "CH_TRADE_HIGH_PRICE": float(spot), "CH_TRADE_LOW_PRICE": float(spot),
                "CH_CLOSING_PRICE": float(spot),
                "CH_PREVIOUS_CLS_PRICE": float(history["CH_CLOSING_PRICE"].iloc[-1]),
                "CH_TOT_TRADED_QTY": 0,
            })
            return pd.concat([history, pd.DataFrame([row])], ignore_index=True)
        df = history.copy()
        idx = df.index[-1]
        df.loc[idx, "CH_CLOSING_PRICE"] = float(spot)
        df.loc[idx, "CH_TRADE_HIGH_PRICE"] = max(float(df.loc[idx, "CH_TRADE_HIGH_PRICE"]), float(spot))
        df.loc[idx, "CH_TRADE_LOW_PRICE"] = min(float(df.loc[idx, "CH_TRADE_LOW_PRICE"]), float(spot))
        return df

    def _load_iv_map(self, symbol: str, expiry: str) -> dict:
        """Best-effort IV lookup via the optionGreek endpoint, keyed by
        (strike, option_type). Returns {} on any failure - IV just scores
        neutral (50th percentile) if unavailable rather than breaking the run.
        Cached briefly (5 min). Only called when config.FETCH_IV is True."""
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
        """SmartAPI's quote call only gives today's current OI, not the change -
        so we track our own readings and diff against the earliest reading of
        the day (a simple stand-in for 'OI buildup since morning').
        Kept in memory during a scan and written to disk once per scan
        (flush_oi_baseline) instead of rewriting the file for every option."""
        today = dt.date.today().isoformat()
        if self._oi_state is None:
            self._oi_state = {}
            if os.path.exists(cache_file):
                try:
                    with open(cache_file) as f:
                        self._oi_state = json.load(f)
                except Exception:
                    self._oi_state = {}
        day_entry = self._oi_state.setdefault(today, {})
        first_oi = day_entry.get(token)
        if first_oi is None:
            day_entry[token] = current_oi
            first_oi = current_oi
            self._oi_dirty = True
        if not first_oi:
            return 0.0
        return round((current_oi - first_oi) / first_oi * 100, 2)

    def flush_oi_baseline(self, cache_file: str = "angelone_oi_history.json"):
        if not self._oi_dirty or self._oi_state is None:
            return
        # keep only today + yesterday to stop the file growing forever
        self._oi_state = {k: v for k, v in sorted(self._oi_state.items())[-2:]}
        try:
            with open(cache_file, "w") as f:
                json.dump(self._oi_state, f)
        except Exception as exc:
            print(f"[angelone] could not save OI baseline (not fatal): {exc}")
        self._oi_dirty = False

    # -- option chain helpers --------------------------------------------
    def _pick_near_atm(self, symbol: str, is_index: bool, spot: float, strikes_each_side: int = None):
        """Nearest-expiry option instruments around the money: (expiry, [instruments])."""
        instruments = self._find_option_instruments(symbol, is_index)
        if not instruments:
            return None, []
        today = dt.date.today()
        expiries = sorted(set(i["expiry"] for i in instruments if i.get("expiry")),
                          key=self._parse_expiry)
        expiries = [e for e in expiries if self._parse_expiry(e) >= today] or expiries
        if not expiries:
            return None, []
        nearest_expiry = expiries[0]
        this_expiry = [i for i in instruments if i["expiry"] == nearest_expiry]

        # Fixed-count window around ATM instead of a %-band - a %-band scales
        # unpredictably with each stock's strike spacing and is what blew past
        # Angel One's per-call token limit on a stock with fine strike steps.
        n = self.STRIKES_EACH_SIDE if strikes_each_side is None else strikes_each_side
        near_atm = []
        for opt_type in ("CE", "PE"):
            side = [i for i in this_expiry if i["symbol"].endswith(opt_type)]
            side.sort(key=lambda i: abs(float(i["strike"]) / 100 - spot))
            near_atm.extend(side[: n * 2 + 1])
        return nearest_expiry, near_atm

    def _quotes_from_rows(self, symbol: str, expiry: str, near_atm: list, rows: dict) -> list:
        iv_map = self._load_iv_map(symbol, expiry) if config.FETCH_IV else {}
        quotes = []
        for inst in near_atm:
            row = rows.get(str(inst["token"]))
            if not row:
                continue
            strike = float(inst["strike"]) / 100  # Angel One stores strike * 100
            option_type = "CE" if inst["symbol"].endswith("CE") else "PE"
            current_oi = int(row.get("opnInterest", 0) or 0)
            quotes.append(OptionQuote(
                symbol=symbol,
                expiry=inst.get("expiry") or expiry,
                strike=strike,
                option_type=option_type,
                ltp=float(row.get("ltp", 0)),
                oi=current_oi,
                oi_change_pct=self._oi_change_pct(inst["token"], current_oi),
                iv=iv_map.get((strike, option_type), 0.0),
                volume=int(row.get("tradeVolume", 0) or 0),
                lot_size=int(float(inst.get("lotsize", 1) or 1)),   # from the instrument master
            ))
        return quotes

    def _get_option_chain(self, symbol: str, is_index: bool, spot: float) -> list:
        """Single-symbol path (used by get_underlying_snapshot / diagnostics)."""
        nearest_expiry, near_atm = self._pick_near_atm(symbol, is_index, spot)
        if not near_atm:
            return []
        # VERIFIED via diagnose_angelone.py: ltpData does NOT include OI/volume
        # for options - only getMarketData("FULL", ...) does, under
        # data.fetched[i].opnInterest / .tradeVolume (confirmed field names).
        rows = self._market_data("NFO", [i["token"] for i in near_atm])
        return self._quotes_from_rows(symbol, nearest_expiry, near_atm, rows)

    def _find_contract(self, symbol: str, strike: float, option_type: str, expiry: str = None,
                       tol: float = 1e-6):
        """Instrument for one option: closest strike within `tol` (exact by default), on the given
        expiry or else the nearest live expiry. None if there is no such contract."""
        today = dt.date.today()
        best = None   # (strike difference, expiry date, instrument)
        for t in ("OPTIDX", "OPTSTK"):
            for i in self._opt_index.get((t, symbol.upper()), []):
                if not i["symbol"].endswith(option_type):
                    continue
                diff = abs(float(i["strike"]) / 100 - strike)
                if diff > tol:
                    continue
                if expiry is not None:
                    if i.get("expiry") != expiry:
                        continue
                    e = today
                else:
                    try:
                        e = self._parse_expiry(i["expiry"])
                    except Exception:
                        continue
                    if e < today:
                        continue
                if best is None or (round(diff, 6), e) < (round(best[0], 6), best[1]):
                    best = (diff, e, i)
        return best[2] if best else None

    def contract_info(self, symbol: str, strike: float, option_type: str):
        """(expiry, lot_size, actual_strike) of the nearest-expiry contract, from the instrument master
        (no API call). A strike typed as a rounded number still matches (alerts used to show 307.5 as
        '308'): the closest strike within 0.5 is used."""
        inst = (self._find_contract(symbol, strike, option_type)
                or self._find_contract(symbol, strike, option_type, tol=0.51))
        if not inst:
            return None
        return inst["expiry"], int(float(inst.get("lotsize", 1) or 1)), float(inst["strike"]) / 100

    # -- universe --------------------------------------------------------
    def get_universe(self) -> list:
        """[(symbol, is_index), ...] to scan today. With config.AUTO_UNIVERSE the
        stock list is built from the live F&O contract list, keeping the most
        liquid names (by open interest value) plus your curated list; otherwise
        the curated config list is used as-is. Built once per day."""
        today = dt.date.today()
        if self._universe_cache and self._universe_cache[0] == today:
            return self._universe_cache[1]
        core = [(s, True) for s in config.INDEX_UNIVERSE] + [(s, False) for s in config.STOCK_UNIVERSE]
        universe = core
        if config.AUTO_UNIVERSE:
            try:
                universe = self._build_auto_universe(core)
            except Exception as exc:
                print(f"[angelone] auto-universe failed ({exc}); using the curated list instead.")
                universe = core
        self._universe_cache = (today, universe)
        return universe

    def _build_auto_universe(self, core: list) -> list:
        curated_stocks = [s for s, is_idx in core if not is_idx]
        candidates = sorted(n for (t, n) in self._opt_index if t == "OPTSTK" and n in self._eq_index)
        if not candidates:
            raise RuntimeError("no F&O stocks found in the instrument master")

        spots = self._market_data("NSE", [self._eq_index[n]["token"] for n in candidates])
        meta, tokens = {}, []
        for n in candidates:
            row = spots.get(str(self._eq_index[n]["token"]))
            if not row or not row.get("ltp"):
                continue
            spot = float(row["ltp"])
            _, near = self._pick_near_atm(n, False, spot, strikes_each_side=1)   # 3 strikes x CE/PE
            if near:
                meta[n] = (spot, near)
                tokens.extend(i["token"] for i in near)
        if not meta:
            raise RuntimeError("no live prices returned for the F&O stock list")

        rows = self._market_data("NFO", list(dict.fromkeys(tokens)))
        scored = []
        for n, (spot, near) in meta.items():
            oi = sum(int(rows.get(str(i["token"]), {}).get("opnInterest", 0) or 0) for i in near)
            lot = int(float(near[0].get("lotsize", 1) or 1))
            scored.append((oi * lot * spot, n))      # open interest value in rupees
        scored.sort(reverse=True)

        top = [n for _, n in scored[:config.AUTO_UNIVERSE_MAX_STOCKS]]
        stocks = list(dict.fromkeys(top + [s for s in curated_stocks if s in meta]))
        dropped = [s for s in curated_stocks if s not in meta]
        print(f"[angelone] auto-universe: {len(candidates)} F&O stocks found, scanning "
              f"{len(stocks)} (top {config.AUTO_UNIVERSE_MAX_STOCKS} by open interest value + your curated list)."
              + (f" Curated symbols with no live options, skipped: {dropped}" if dropped else ""))
        return [(s, True) for s in config.INDEX_UNIVERSE] + [(s, False) for s in stocks]

    def get_intraday_context(self, symbol: str, is_index: bool = False):
        """The underlying's 15-MINUTE picture right now, for the journal and the alert text (it does NOT change any score):
        EMA9 vs EMA20 on 15-minute candles and the price vs the day's VWAP. None if it cannot be read."""
        try:
            inst = self._find_equity_or_index_token(symbol, is_index)
            now = dt.datetime.now()
            params = {
                "exchange": "NSE", "symboltoken": inst["token"], "interval": "FIFTEEN_MINUTE",
                "fromdate": (now - dt.timedelta(days=5)).strftime("%Y-%m-%d 09:15"),
                "todate": now.strftime("%Y-%m-%d %H:%M"),
            }
            resp = _throttled_candle_call(self.smart_api.getCandleData, params)
            rows = resp.get("data", []) if resp else []
            if len(rows) < 21:
                return None
            closes = pd.Series([float(r[4]) for r in rows])
            e9, e20 = ema(closes, 9).iloc[-1], ema(closes, 20).iloc[-1]
            today = now.date().isoformat()
            today_rows = [r for r in rows if str(r[0])[:10] == today]
            vol = sum(float(r[5] or 0) for r in today_rows)
            vwap = (sum((float(r[2]) + float(r[3]) + float(r[4])) / 3 * float(r[5] or 0) for r in today_rows) / vol) if vol > 0 else None
            last = float(closes.iloc[-1])
            return {"ema9_15m": round(float(e9), 2), "ema20_15m": round(float(e20), 2), "ema_up_15m": bool(e9 > e20),
                    "vwap": round(vwap, 2) if vwap else None, "above_vwap": (last >= vwap) if vwap else None,
                    "last": round(last, 2), "candles_today": len(today_rows), "asof": now.strftime("%H:%M")}
        except Exception as exc:
            print(f"[angelone] intraday context for {symbol} unavailable: {exc}")
            return None

    def traded_today(self):
        """True/False: does NIFTY have a daily candle dated today (i.e. NSE is
        actually trading)? None if it can't be determined (the caller should then
        carry on as normal - this check must never block a real trading day)."""
        try:
            inst = self._find_equity_or_index_token("NIFTY", True)
            today = dt.date.today()
            params = {
                "exchange": "NSE", "symboltoken": inst["token"], "interval": "ONE_DAY",
                "fromdate": (today - dt.timedelta(days=7)).strftime("%Y-%m-%d 09:00"),
                "todate": today.strftime("%Y-%m-%d 15:30"),
            }
            resp = _throttled_call(self.smart_api.getCandleData, params)
            candles = resp.get("data", []) if resp else []
            if not candles:
                return None
            return str(candles[-1][0])[:10] == today.isoformat()
        except Exception as exc:
            print(f"[angelone] could not check whether the market traded today: {exc}")
            return None

    def _prefetch_histories(self, jobs: list):
        """Download daily history for many symbols a few at a time (jobs = [(exchange, token), ...]).
        Failures are ignored here - the normal per-symbol path retries and reports them."""
        todo = [(ex, tok) for ex, tok in dict.fromkeys(jobs)
                if not ((c := self._history_cache.get((ex, tok))) and
                        time.time() - c[0] < self._history_cache_max_age)]
        if not todo:
            return
        def one(job):
            try:
                self._get_historical_daily(*job)
            except Exception:
                pass
        started = time.time()
        with ThreadPoolExecutor(max_workers=CANDLE_WORKERS) as pool:
            list(pool.map(one, todo))
        print(f"[angelone] loaded history for {len(todo)} symbols in {time.time() - started:.0f}s")

    @property
    def stats(self) -> dict:
        return dict(STATS)

    # -- snapshots ---------------------------------------------------------
    def preload(self):
        """Download the daily history for every symbol in today's universe (the slow part of the first scan) so the first scan
        is quick. Safe to call before the market opens; a no-op for symbols already cached."""
        universe = self.get_universe()
        if len(universe) < 30:        # suspiciously small (pre-open quotes can be empty): rebuild at the first real scan
            self._universe_cache = None
        jobs = []
        for sym, is_index in universe:
            try:
                jobs.append(("NSE", self._find_equity_or_index_token(sym, is_index)["token"]))
            except Exception:
                pass
        self._prefetch_histories(jobs)

    def get_snapshots(self, universe: list, chain_filter=None) -> list:
        """Fast path: builds snapshots for the whole universe using batched quote calls (~40 tokens per API call) instead of
        several calls per symbol. Symbols that fail are skipped, not fatal.
        chain_filter (optional, two-stage scan): a function(snapshot without option chain) -> bool. Option quotes are downloaded
        only for symbols where it returns True, for indexes and for symbols we hold; the others get an empty option chain."""
        insts = {}
        for sym, is_index in universe:
            try:
                insts[sym] = self._find_equity_or_index_token(sym, is_index)
            except Exception as exc:
                print(f"[angelone] skip {sym}: {exc}")

        spot_rows = self._market_data("NSE", [i["token"] for i in insts.values()])

        plan = []
        for sym, is_index in universe:
            inst = insts.get(sym)
            if not inst:
                continue
            try:
                row = spot_rows.get(str(inst["token"]))
                if row and row.get("ltp"):
                    spot = float(row["ltp"])
                else:   # batch gave nothing for this symbol - fall back to the single call
                    spot = self._get_ltp("NSE", inst["symbol"], inst["token"])
                nearest, near = self._pick_near_atm(sym, is_index, spot)
            except Exception as exc:
                print(f"[angelone] skip {sym}: {exc}")
                continue
            held = []
            have = {i["token"] for i in near}
            for (hs, hk, ht, he) in self.watch_contracts:
                if hs != sym:
                    continue
                hi = self._find_contract(hs, hk, ht, he)
                if hi and hi["token"] not in have:
                    held.append(hi)
                    have.add(hi["token"])
            plan.append((sym, is_index, inst, spot, nearest, near, held))

        self._prefetch_histories([("NSE", inst["token"]) for _, _, inst, *_ in plan])

        def live_volume(inst):
            row_s = spot_rows.get(str(inst["token"])) or {}
            try:
                return int(float(row_s.get("tradeVolume") or 0))
            except (TypeError, ValueError):
                return 0

        # stage 1 -> which symbols need their option chain downloaded
        wanted = set()
        for sym, is_index, inst, spot, nearest, near, held in plan:
            if chain_filter is None or is_index or held or any(h[0] == sym for h in self.watch_contracts):
                wanted.add(sym)
                continue
            try:
                hist0 = self._with_live_today(self._get_historical_daily("NSE", inst["token"]), spot)
                if chain_filter(self._make_snapshot(sym, spot, hist0, [], live_volume=live_volume(inst))):
                    wanted.add(sym)
            except Exception:
                wanted.add(sym)        # when in doubt, download it
        all_tokens = []
        for sym, is_index, inst, spot, nearest, near, held in plan:
            if sym in wanted:
                all_tokens.extend(i["token"] for i in near + held)
        option_rows = self._market_data("NFO", list(dict.fromkeys(all_tokens))) if all_tokens else {}
        if chain_filter is not None:
            print(f"[angelone] two-stage scan: option chains for {len(wanted)} of {len(plan)} symbols")

        snapshots = []
        for sym, is_index, inst, spot, nearest, near, held in plan:
            try:
                history = self._with_live_today(
                    self._get_historical_daily("NSE", inst["token"]), spot)
                get_chain = sym in wanted
                chain = self._quotes_from_rows(sym, nearest, near, option_rows) if (near and get_chain) else []
                held_q = self._quotes_from_rows(sym, nearest, held, option_rows) if (held and get_chain) else []
                snap = self._make_snapshot(sym, spot, history, chain, live_volume=live_volume(inst))
                snap.held_quotes = held_q
                snapshots.append(snap)
            except Exception as exc:
                print(f"[angelone] skip {sym}: {exc}")
        self.flush_oi_baseline()
        return snapshots

    @staticmethod
    def _avg_prior_volume(history) -> float:
        """Average daily volume of the 20 days BEFORE today (today's partial candle must not drag the average down)."""
        if history is None or history.empty:
            return 0.0
        vols = history
        if "CH_TIMESTAMP" in vols and str(vols["CH_TIMESTAMP"].iloc[-1]) == dt.date.today().isoformat() and len(vols) > 1:
            vols = vols.iloc[:-1]
        return float(vols["CH_TOT_TRADED_QTY"].tail(20).mean())

    @staticmethod
    def _make_snapshot(symbol, spot, history, option_chain, live_volume=0) -> UnderlyingSnapshot:
        # Today's volume: the LIVE traded volume from the quote when available. The daily candle is cached for hours,
        # so its volume would be stale (that made morning scores too low and caused an afternoon burst on 26 Nov 2026).
        cached_volume = int(history["CH_TOT_TRADED_QTY"].iloc[-1]) if not history.empty else 0
        return UnderlyingSnapshot(
            symbol=symbol,
            spot=spot,
            prev_close=float(history["CH_PREVIOUS_CLS_PRICE"].iloc[-1]) if not history.empty else spot,
            day_volume=int(live_volume) if live_volume and live_volume > 0 else cached_volume,
            avg_20d_volume=AngelOneDataProvider._avg_prior_volume(history),
            history=history,
            option_chain=option_chain,
        )

    def get_underlying_snapshot(self, symbol: str, is_index: bool = False) -> UnderlyingSnapshot:
        inst = self._find_equity_or_index_token(symbol, is_index)
        exchange = "NSE"
        spot = self._get_ltp(exchange, inst["symbol"], inst["token"])
        history = self._with_live_today(self._get_historical_daily(exchange, inst["token"]), spot)
        option_chain = self._get_option_chain(symbol, is_index, spot)
        self.flush_oi_baseline()
        return self._make_snapshot(symbol, spot, history, option_chain)
