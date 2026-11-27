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

## How you know it is healthy (the daily routine)
- **About 09:15 IST:** Telegram "🟢 Bot started" (from the cron-job.org timer). **No message by 09:25 = start it by hand**
  (Actions -> NSE options signal bot -> Run workflow) and look at the Actions tab.
- **About 09:22-09:30:** "✅ First scan done in X min" (a few minutes at most).
- **During the day:** heads-ups, entry alerts, target/stoploss messages. "⚠️ Data feed is down" means no market data; "⚠️ The 15-minute data ... could not be read" means only the silent index test and the
  15-minute trend line are missing (alerts are not affected).
- **~14:55:** end-of-day check-in for any open trade. **15:10:** every open trade is squared off. **15:12:** "🔚 Session ended" with the day's summary and a "Bot health" block (scans, scan times, rate-limit retries, 15-minute data ok/failed, index regime readings).
- **A few seconds later:** "🧪 Nightly learning" note. **Fridays about 17:00:** "📚 Weekly review" (if it has not arrived by 17:30, run **Weekly trade review** by hand from the Actions tab; GitHub's scheduler can skip it).
- A failed or **cancelled** run sends "Trading bot run FAILED or was CANCELLED" (a job is cancelled if it runs past GitHub's 6-hour limit).
- Each run's `monitor.log` and `paper_trades.csv` are in the run page under **Artifacts** (kept 90 days); the journal, the nightly lab reports, the scanned symbols (`universe-<date>.txt`) and the index regime log are on the `journal-data` branch.

## Free-minute budget (private repo = 2,000 min/month)
The default session is now the **full day**, 09:15-15:12 IST (~6 h/day; GitHub's 6-hour job limit is why the session cannot run later) - only suitable for a
**public** repo (unlimited minutes). On a **private** repo set repo Variables `SESSION_END=10:30` and
`JOB_TIMEOUT_MIN=100` (~80 min/day, ~1,750 min/month). To check usage: Settings -> Billing -> Usage.
- **Public repo** = unlimited minutes (code, logs and artifacts become public; credentials stay safe in Secrets).
- Change the window on a private repo with the `SESSION_END` variable
  (Settings -> Secrets and variables -> Actions -> Variables), but watch the budget.

## Limits to know
- Not tick-live: scan cadence is roughly 1-2 minutes. Signals are for options
  you place yourself by hand; paper fills assume `PAPER_SLIPPAGE_PCT` slippage per side.
- GitHub's scheduler can start a run late or skip it, so the workflow has three backup start times (09:15, 09:20, 09:25 IST) next to the cron-job.org timer. Only one session runs. If nothing has started by ~09:25, press **Run workflow** yourself.
- Telegram commands (`help`, `status`, `trades`, `pnl`, `summary`, `close`) work only while a session is running.
- Angel One may restrict API access by IP; GitHub runners have changing IPs.
  If login fails you will get a "Bot could not start" message.

## What gets scanned
At the start of each day the bot reads Angel One's live F&O contract list, ranks the
stocks by open-interest value (money parked in near-the-money options) and scans the top 120 (effectively every F&O stock),
plus your curated list in `config.py` (if they still have options), plus NIFTY/BANKNIFTY/FINNIFTY.
Prices come ~40 per API call. Settings in `config.py`: `AUTO_UNIVERSE`, `AUTO_UNIVERSE_MAX_STOCKS`, `FETCH_IV`.
The first scan of the day is slower (it downloads daily candles for every symbol once, 2 at a time, mostly before the open). The bot says how long it took ("✅ First scan done in X min") and the 15:10 summary reports first/average scan time, API calls and rate-limit retries. Commands you send meanwhile are answered right away.

## Daily summary and holidays
- **Summary:** sent when the session ends (15:12 IST with the default full day, or `SESSION_END`; every open paper trade is squared off at 15:10),
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

---

## Reliable daily start: outside timer (cron-job.org) + early backup slots

GitHub's own scheduler is best-effort (on 25-26 Nov 2026 it started runs up to 9 hours late or not at all).
`bot.yml` therefore has three backup slots (09:15, 09:20, 09:25 IST), **and** a free outside timer calls GitHub at the
right minute. **Do not start earlier than about 09:13:** GitHub stops a hosted job 6 hours after it starts, so an earlier start is cut off before the 15:10 square-off (27 Nov 2026: started 09:00, killed at 15:00:39). The bot now protects itself (it ends early and squares off, with a warning), but a 09:15 start gives the whole session.

**1. Create a GitHub token (once)**
GitHub -> your profile picture -> Settings -> Developer settings -> Personal access tokens -> *Fine-grained tokens*
-> Generate new token. Repository access: **Only select repositories -> Indian_Trading_Bot**. Permissions ->
Repository permissions -> **Actions: Read and write** (nothing else). Expiration: the longest offered (set a
reminder to renew it). Copy the token (`github_pat_...`). Never paste it in chat, in the repo, or in Telegram.

**2. Create the timer on cron-job.org (free account)**
Create cronjob with:
- URL: `https://api.github.com/repos/IvanAjith/Indian_Trading_Bot/actions/workflows/bot.yml/dispatches`
- Schedule (Advanced / custom): time zone **Asia/Kolkata**, Monday-Friday, **09:15** (crontab `15 9 * * 1-5`) and a second backup job at **09:25** (`25 9 * * 1-5`)
- Request method: **POST**; request body: `{"ref":"main"}`
- Headers:
  - `Authorization: Bearer <your token>`
  - `Accept: application/vnd.github+json`
  - `X-GitHub-Api-Version: 2022-11-28`
  - `User-Agent: nse-bot-timer`
  - `Content-Type: application/json`
- Turn on "notify me on failure".
GitHub answers **204 No Content** when it worked. Use the "Test run" button once (on a trading day before 15:12 this
really starts a session - that is fine).

Extra starts are harmless: only one session runs at a time and a queued extra exits once the session is over.

### Optional extra timers (same token, same headers and body `{"ref":"main"}`; only the URL and the time change)
GitHub's scheduler skipped the Friday weekly review on 27 Nov (no run at all), and delayed the 8 PM holiday notice by almost 9 hours. If you want them to be as reliable as the daily start, add two more cron-job.org jobs (time zone Asia/Kolkata):
- **Weekly review:** URL `https://api.github.com/repos/IvanAjith/Indian_Trading_Bot/actions/workflows/weekly-review.yml/dispatches`, Fridays **17:00** (`0 17 * * 5`).
- **Holiday notice:** URL `https://api.github.com/repos/IvanAjith/Indian_Trading_Bot/actions/workflows/holiday-notice.yml/dispatches`, every day **20:00** (`0 20 * * *`). Only matters before an NSE holiday (the next one in the list is 25 Dec); the bot itself also detects a closed market at 09:20 and stands down.

