"""
diagnose_nse.py
---------------
Run this on your own machine to see EXACTLY what NSE is sending back, instead
of guessing from a KeyError or a bare 404. Paste the output back if things
are still failing after data_fetcher.py's retry/referer fix.

    python3 diagnose_nse.py
    python3 diagnose_nse.py RELIANCE
"""

import sys
import requests

BASE = "https://www.nseindia.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "application/json, text/plain, */*",
    "X-Requested-With": "XMLHttpRequest",
}


def show(label, resp):
    print(f"\n--- {label} ---")
    print("status:", resp.status_code)
    print("content-type:", resp.headers.get("content-type"))
    print("cookies set:", list(resp.cookies.keys()) or list(resp.request._cookies.keys() if hasattr(resp.request, '_cookies') else []))
    print("body (first 400 chars):")
    print(resp.text[:400])


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    session = requests.Session()
    session.headers.update(HEADERS)

    home = session.get(BASE, timeout=10)
    show("GET / (homepage warm-up)", home)
    print("session cookies after homepage:", list(session.cookies.keys()))

    quote_page = session.get(f"{BASE}/get-quote/equity/{symbol}", timeout=10)
    show(f"GET /get-quote/equity/{symbol} (referer warm-up)", quote_page)
    print("session cookies after quote page:", list(session.cookies.keys()))

    session.headers["Referer"] = f"{BASE}/get-quote/equity/{symbol}"
    api_resp = session.get(f"{BASE}/api/option-chain-equities?symbol={symbol}", timeout=10)
    show(f"GET /api/option-chain-equities?symbol={symbol}", api_resp)

    idx_page = session.get(f"{BASE}/option-chain?symbol=NIFTY", timeout=10)
    show("GET /option-chain?symbol=NIFTY (index referer warm-up)", idx_page)

    session.headers["Referer"] = f"{BASE}/option-chain?symbol=NIFTY"
    idx_api = session.get(f"{BASE}/api/option-chain-indices?symbol=NIFTY", timeout=10)
    show("GET /api/option-chain-indices?symbol=NIFTY", idx_api)

    print(
        "\nIf any 'status' above is 401/403, or the body looks like an HTML "
        "page (starts with '<') or a captcha/'Access Denied' message instead "
        "of JSON starting with '{', NSE is blocking this as a bot request. "
        "That's the known limitation of the unofficial API — the reliable "
        "fix at that point is a broker data feed (Upstox/Angel One), not "
        "more header tweaking."
    )


if __name__ == "__main__":
    main()
