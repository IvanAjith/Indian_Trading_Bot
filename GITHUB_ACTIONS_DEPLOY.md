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
Default session is 09:15-10:30 IST (~80 min/day, ~1,750 min/month): the most
active part of the day. To check usage: Settings -> Billing -> Usage.
- **Public repo** = unlimited minutes. Then set repo *Variables*
  `SESSION_END=15:15` and `JOB_TIMEOUT_MIN=360` for a (nearly) full day.
  (Code becomes public; credentials stay safe in Secrets.)
- Change the window on a private repo with the `SESSION_END` variable
  (Settings -> Secrets and variables -> Actions -> Variables), but watch the budget.

## Limits to know
- Not tick-live: scan cadence is roughly 1-2 minutes. Signals are for options
  you place yourself by hand; paper fills assume `PAPER_SLIPPAGE_PCT` slippage per side.
- GitHub cron can start a few minutes late.
- Telegram commands (`status`, `summary`, `hold`, `close`) work only while a session is running.
- Angel One may restrict API access by IP; GitHub runners have changing IPs.
  If login fails you will get a "Bot could not start" message.

## What gets scanned
At the start of each day the bot reads Angel One's live F&O contract list, ranks the
stocks by open-interest value (money parked in near-the-money options) and scans the top 80,
plus your curated list in `config.py` (if they still have options), plus NIFTY/BANKNIFTY/FINNIFTY.
Prices come ~40 per API call. Settings in `config.py`: `AUTO_UNIVERSE`, `AUTO_UNIVERSE_MAX_STOCKS`, `FETCH_IV`.
The first scan of the day is slower (it downloads daily candles for every symbol once).
