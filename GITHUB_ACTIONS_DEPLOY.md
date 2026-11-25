# Running the bot on GitHub Actions (free)

The bot runs as a scheduled GitHub job: every trading day at ~09:15 IST it
starts, scans continuously (a 10-second pause plus the scan itself, which takes roughly a minute for the full ~85-symbol list - an estimate, check monitor.log for the real figure), sends
Telegram alerts, and ends itself at the session end time, closing any open
paper positions and sending a summary.

## One-time setup (you do this; secrets are never shared in chat)

1. Repo **Settings -> Secrets and variables -> Actions -> New repository secret**.
   Add all six:
   `ANGELONE_API_KEY`, `ANGELONE_CLIENT_CODE`, `ANGELONE_PIN`,
   `ANGELONE_TOTP_SECRET`, `TB_TELEGRAM_TOKEN`, `TB_TELEGRAM_CHAT_ID`.
2. Merge this branch into `main` (GitHub only runs scheduled workflows from the default branch).
3. **Actions tab -> "NSE options signal bot" -> Run workflow** to test now.
   (Outside market hours it will start and just idle until the session end
   time you type in - use a time a few minutes ahead, e.g. now + 5 min.)
4. **Stop any old copy of the bot** (old Mac/VPS) - two copies fight over Telegram.

## How you know it is healthy
- 09:15-09:30 IST: Telegram message "Bot started". **No message = something is wrong**
  (check the Actions tab; GitHub also emails you when a run fails).
- A failed run sends "Trading bot run FAILED". A dead data feed sends "Data feed is down".
- End of session: "Session ended" + the day's summary.
- Each run's `monitor.log` and `paper_trades.csv` are in the run page under **Artifacts** (kept 90 days).

## Free-minute budget (private repo = 2,000 min/month)
The default session is now the **full day**, 09:15-15:10 IST (~6 h/day) - only suitable for a
**public** repo (unlimited minutes). On a **private** repo set repo Variables `SESSION_END=10:30` and
`JOB_TIMEOUT_MIN=100` (~80 min/day, ~1,750 min/month). To check usage: Settings -> Billing -> Usage.
- **Public repo** = unlimited minutes (code, logs and artifacts become public; credentials stay safe in Secrets).
- Change the window on a private repo with the `SESSION_END` variable
  (Settings -> Secrets and variables -> Actions -> Variables), but watch the budget.

## Limits to know
- Not tick-live: scan cadence is roughly 1-2 minutes. Signals are for options
  you place yourself by hand; paper fills assume `PAPER_SLIPPAGE_PCT` slippage per side.
- GitHub's scheduler can start a run late or skip it, so the workflow has four start times (09:15, 09:20, 09:25, 09:30 IST). Only one session runs. If nothing has started by ~09:45, press **Run workflow** yourself.
- Telegram commands (`help`, `status`, `trades`, `pnl`, `summary`, `close`) work only while a session is running.
- Angel One may restrict API access by IP; GitHub runners have changing IPs.
  If login fails you will get a "Bot could not start" message.

## What gets scanned
At the start of each day the bot reads Angel One's live F&O contract list, ranks the
stocks by open-interest value (money parked in near-the-money options) and scans the top 80,
plus your curated list in `config.py` (if they still have options), plus NIFTY/BANKNIFTY/FINNIFTY.
Prices come ~40 per API call. Settings in `config.py`: `AUTO_UNIVERSE`, `AUTO_UNIVERSE_MAX_STOCKS`, `FETCH_IV`.
The first scan of the day is slower (it downloads daily candles for every symbol once, 3 at a time). The bot says how long it took ("✅ First scan done in X min") and the 15:10 summary reports first/average scan time, API calls and rate-limit retries. Commands you send meanwhile are answered right away.

## Daily summary and holidays
- **Summary:** sent when the session ends (15:10 IST with the default full day, or `SESSION_END`),
  with signals, exits, P&L and a bot-health line (scans completed, symbols per scan).
- **Holiday in the morning:** the bot waits until 09:20, checks that NIFTY actually has a candle for
  today, and if not sends "Market closed today" and ends (no stale-price signals). If the check cannot
  be answered it carries on normally, so it can never block a real trading day.
- **Holiday notice at 20:00 IST:** workflow `holiday-notice.yml` sends "Market closed tomorrow - <reason>"
  only when tomorrow is a weekday NSE holiday. It reads NSE's official list; if NSE blocks GitHub, it
  falls back to `market_holidays.json` - add weekday holidays there as `"YYYY-MM-DD": "Reason"` (copy
  from nseindia.com -> Resources -> Exchange Communication -> Holidays). NSE publishes next year's list
  around December, so refresh the file then. Test it: Actions -> "Market holiday notice" -> Run
  workflow with `today` = the day before a holiday, e.g. `2026-12-24`.
