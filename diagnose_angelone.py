"""
diagnose_angelone.py
---------------------
Run this FIRST, before trusting angelone_provider.py for real signals.
It logs in, fetches one spot LTP, one day of candles, and one option quote,
and prints the RAW response for each -- so we can confirm field names
(opnInterest vs oi, etc.) match what angelone_provider.py expects, and fix
anything that doesn't in one pass.

Requires the same environment variables as the main bot:
    ANGELONE_API_KEY, ANGELONE_CLIENT_CODE, ANGELONE_PIN, ANGELONE_TOTP_SECRET

Usage:
    python3 diagnose_angelone.py            # defaults to RELIANCE
    python3 diagnose_angelone.py INFY
"""

import sys
import json
import datetime as dt

from angelone_provider import _require_credentials, _throttled_call, INDEX_INSTRUMENT_TYPE

INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY"}


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    is_index = symbol.upper() in INDEX_SYMBOLS
    opt_type = "OPTIDX" if is_index else "OPTSTK"

    try:
        from SmartApi import SmartConnect
        import pyotp
    except ImportError:
        print("Run: pip3 install smartapi-python pyotp")
        return

    creds = _require_credentials()
    smart_api = SmartConnect(creds["api_key"])
    totp = pyotp.TOTP(creds["totp_secret"]).now()

    print("--- Logging in ---")
    session = smart_api.generateSession(creds["client_code"], creds["pin"], totp)
    print(json.dumps(session, indent=2, default=str)[:1000])
    if not session or not session.get("status"):
        print("\nLogin failed - double check ANGELONE_CLIENT_CODE / ANGELONE_PIN / "
              "ANGELONE_TOTP_SECRET are current (TOTP secrets don't expire, but a "
              "wrong PIN or stale API key will fail here).")
        return

    print("\n--- Fetching instrument master (may take a few seconds) ---")
    import requests
    resp = requests.get(
        "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json",
        timeout=30,
    )
    instruments = resp.json()
    print(f"Loaded {len(instruments)} instruments.")

    if is_index:
        print(f"'{symbol}' recognized as an index -> matching instrument name '{symbol.upper()}' "
              f"directly (confirmed: index 'name' field IS the plain symbol, e.g. name='NIFTY')")
        eq_matches = [i for i in instruments
                      if i.get("exch_seg") == "NSE" and i.get("name", "").upper() == symbol.upper()
                      and i.get("instrumenttype", "") == INDEX_INSTRUMENT_TYPE]
    else:
        eq_matches = [i for i in instruments if i.get("symbol") == f"{symbol}-EQ"]
    print(f"Sample match:", json.dumps(eq_matches[:1], indent=2))

    if not eq_matches:
        print(f"\nNo spot instrument found for {symbol} via the expected filter.")
        if is_index:
            # Broad substring search instead of guessing the exact fields again -
            # this shows us the REAL representation so we can fix
            # INDEX_SPOT_SYMBOLS/the matching filter in one pass.
            root = symbol.upper().replace("NIFTY", "").strip() or "NIFTY"
            hits = [i for i in instruments
                    if "NIFTY" in i.get("name", "").upper() or "NIFTY" in i.get("symbol", "").upper()]
            index_hits = [i for i in hits if i.get("instrumenttype", "") in ("", "AMXIDX", "INDEX")
                          and i.get("strike", "-1") in ("-1.000000", "0.000000", "")]
            print(f"\n--- Broad search: entries with 'NIFTY' in name/symbol that look like an index "
                  f"(not an option/future) - showing up to 15 ---")
            print(json.dumps(index_hits[:15], indent=2))
            print(
                "\nFind the row that matches this symbol (NIFTY/BANKNIFTY/FINNIFTY) above, "
                "then tell me its exact 'name' and 'exch_seg' values and I'll fix "
                "INDEX_SPOT_SYMBOLS / the matching filter in angelone_provider.py to use them."
            )
        else:
            print("Check the symbol spelling, or it may have changed due to a corporate "
                  "action (merger/demerger/renaming) - worth checking manually if a stock "
                  "you expect keeps failing.")
        return

    token = eq_matches[0]["token"]
    tradingsymbol = eq_matches[0]["symbol"]
    spot_exchange = "NSE"

    print(f"\n--- LTP for {tradingsymbol} (token {token}) ---")
    ltp_resp = _throttled_call(smart_api.ltpData, spot_exchange, tradingsymbol, token)
    print(json.dumps(ltp_resp, indent=2, default=str))

    print(f"\n--- One day of candles for {tradingsymbol} (throttled - may pause a few seconds) ---")
    to_date = dt.date.today()
    from_date = to_date - dt.timedelta(days=5)
    candle_resp = _throttled_call(smart_api.getCandleData, {
        "exchange": spot_exchange,
        "symboltoken": token,
        "interval": "ONE_DAY",
        "fromdate": from_date.strftime("%Y-%m-%d 09:00"),
        "todate": to_date.strftime("%Y-%m-%d 15:30"),
    })
    print(json.dumps(candle_resp, indent=2, default=str)[:1000])

    print(f"\n--- {symbol} option instruments in the instrument master (NFO only, excluding BSE/BFO) ---")
    opt_matches = [i for i in instruments
                   if i.get("instrumenttype") == opt_type and i.get("name", "").upper() == symbol.upper()
                   and i.get("exch_seg") == "NFO"]

    def _parse_expiry(expiry):
        return dt.datetime.strptime(expiry, "%d%b%Y").date()

    expiries = sorted(set(i["expiry"] for i in opt_matches if i.get("expiry")), key=_parse_expiry)
    print(f"Found {len(opt_matches)} option contracts across expiries: {expiries[:8]}"
          + (" ..." if len(expiries) > 8 else ""))
    print(
        "(If this list only shows one expiry per month with none in between, that "
        "confirms stock options are monthly-only now, not weekly - config.py's "
        "PREFERRED_EXPIRY assumption would need adjusting for individual stocks, "
        "though index options like NIFTY/BANKNIFTY may still have weekly expiries.)"
    )

    if opt_matches and expiries:
        nearest_expiry = expiries[0]
        same_expiry = [i for i in opt_matches if i["expiry"] == nearest_expiry]
        spot = ltp_resp["data"]["ltp"]
        atm = min(same_expiry, key=lambda i: abs(float(i["strike"]) / 100 - spot))
        print(f"\n--- Sample option instrument (nearest expiry {nearest_expiry}, closest to spot) ---")
        print(json.dumps(atm, indent=2))

        print(f"\n--- LTP/quote for {atm['symbol']} (throttled) ---")
        opt_ltp = _throttled_call(smart_api.ltpData, "NFO", atm["symbol"], atm["token"])
        print(json.dumps(opt_ltp, indent=2, default=str))
        print("(NOTE: ltpData has no OI/volume field for options - that's expected, "
              "see getMarketData below instead.)")

        print(f"\n--- getMarketData FULL quote for {atm['symbol']} (throttled) ---")
        try:
            market_resp = _throttled_call(
                smart_api.getMarketData, "FULL", {"NFO": [atm["token"]]}
            )
            print(json.dumps(market_resp, indent=2, default=str)[:2000])
        except Exception as exc:
            print(f"getMarketData call failed: {exc}")

        print(f"\n--- optionGreek for {symbol} / {nearest_expiry} (throttled) ---")
        try:
            greek_resp = _throttled_call(smart_api.optionGreek, {"name": symbol, "expirydate": nearest_expiry})
            print(json.dumps(greek_resp, indent=2, default=str)[:1500])
        except Exception as exc:
            print(f"optionGreek call failed: {exc}")
    else:
        print(f"\nNo option instruments found for {symbol} - can't test option LTP/greeks.")

    print(
        "\n--- What I need from the getMarketData output above ---\n"
        "Look inside data.fetched[0] (or wherever the quote for our token landed) "
        "for a field holding open interest - likely named opnInterest, openInterest, "
        "or oi - and one for today's traded volume (tradeVolume / tradedVolume). "
        "Tell me the exact key names and I'll switch angelone_provider.py from the "
        "per-instrument ltpData loop to a single batched getMarketData call, which "
        "is both correct (has OI, unlike ltpData) and faster (fewer API calls)."
    )


if __name__ == "__main__":
    main()
