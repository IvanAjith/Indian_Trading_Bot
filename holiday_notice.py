"""
holiday_notice.py
-----------------
Run at ~8 PM IST by .github/workflows/holiday-notice.yml. If tomorrow is a
weekday on which NSE is closed, sends a Telegram message saying why and when
trading resumes. Sends nothing on normal days.

    python holiday_notice.py            # tomorrow, based on today's IST date
    python holiday_notice.py 2026-12-25 # pretend "today" is this date (test)
"""

import sys
import datetime as dt

import market_calendar as mc
from telegram_notifier import send_telegram_message


def build_message(today: dt.date, holidays: dict, source: str):
    tomorrow = today + dt.timedelta(days=1)
    if tomorrow.weekday() >= 5:
        return None                      # weekend - nothing to announce
    reason = holidays.get(tomorrow.isoformat())
    if not reason:
        return None
    resume = mc.next_trading_day(tomorrow, holidays)
    return (
        f"\U0001F3D6️ <b>Market closed tomorrow</b>\n"
        f"{tomorrow:%A, %d %b %Y} - NSE holiday: <b>{reason}</b>\n\n"
        f"No signals tomorrow; the bot will stay idle.\n"
        f"Next trading session: {resume:%A, %d %b} (bot starts ~09:15 IST).\n"
        f"<i>Source: {source}</i>"
    )


def main():
    today = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.date.today()
    holidays, source = mc.load_holidays()
    print(f"[holiday_notice] today={today} source={source} entries={len(holidays)}")
    msg = build_message(today, holidays, source)
    if msg:
        print(msg)
        send_telegram_message(msg)
    else:
        print("[holiday_notice] tomorrow is a normal trading day (or a weekend) - nothing to send.")


if __name__ == "__main__":
    main()
