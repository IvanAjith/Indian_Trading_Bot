"""
market_calendar.py
------------------
NSE trading-holiday lookup, used ONLY for the evening "market closed tomorrow"
notice. It is deliberately NOT used to decide whether the bot trades: the bot
checks live data for that (see AngelOneDataProvider.traded_today), so a wrong
or missing entry here can never stop a real trading day.

Sources, in order:
  1. NSE's own holiday API (official, but NSE sometimes blocks cloud IPs).
  2. market_holidays.json in this repo (you maintain it - format below).
"""

import json
import os
import datetime as dt

import requests

HOLIDAY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_holidays.json")
NSE_URL = "https://www.nseindia.com/api/holiday-master?type=trading"


def _from_nse(timeout: int = 15) -> dict:
    s = requests.Session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    s.get("https://www.nseindia.com/resources/exchange-communication-holidays", timeout=timeout)
    resp = s.get(NSE_URL, timeout=timeout, headers={"Referer": "https://www.nseindia.com/"})
    resp.raise_for_status()
    return parse_nse_payload(resp.json())


def parse_nse_payload(payload: dict) -> dict:
    """{'FO': [{'tradingDate': '26-Jan-2026', 'description': 'Republic Day'}, ...], ...}
    -> {'2026-01-26': 'Republic Day'}. F&O list preferred, equity as fallback."""
    for key in ("FO", "CM"):
        rows = payload.get(key) or []
        out = {}
        for r in rows:
            try:
                d = dt.datetime.strptime(r["tradingDate"], "%d-%b-%Y").date()
            except (KeyError, ValueError):
                continue
            out[d.isoformat()] = str(r.get("description") or "Exchange holiday").strip()
        if out:
            return out
    return {}


def _from_file(path: str = HOLIDAY_FILE) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def load_holidays() -> tuple:
    """Returns (holidays {'YYYY-MM-DD': 'Reason'}, source_label)."""
    try:
        h = _from_nse()
        if h:
            return h, "NSE official list"
    except Exception as exc:
        print(f"[calendar] NSE holiday list unavailable ({exc}); trying market_holidays.json")
    h = _from_file()
    return h, ("market_holidays.json" if h else "no holiday data")


def is_trading_day(day: dt.date, holidays: dict) -> bool:
    return day.weekday() < 5 and day.isoformat() not in holidays


def next_trading_day(after: dt.date, holidays: dict) -> dt.date:
    d = after + dt.timedelta(days=1)
    for _ in range(15):
        if is_trading_day(d, holidays):
            return d
        d += dt.timedelta(days=1)
    return d
