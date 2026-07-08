# NSE F&O Intraday Options Screener/Signal Bot (v0 — paper-trading stage)

Rules-based scanner for NSE F&O stocks/indices that scores momentum, volume,
OI buildup, and IV percentile, then proposes a CE/PE with entry, three
staggered profit targets (T1/T2/T3), and a stoploss — plus the explicit
technical reason each signal fired. Built for **intraday, weekly-expiry
options**.

**No version of this — or any bot — can give you a "very high chance" of
hitting target on every trade.** What it can do is apply the same rules
every day without emotion, alert you on Telegram when they fire, and give
you honest, visual, logged statistics on how those rules actually perform —
so you can decide if there's real edge before risking money.

**About the "score" you see next to each signal (e.g. `score 68.9`):** this
is NOT a win-probability or accuracy number. It's a composite of how many
technical conditions lined up (momentum 40%, volume 20%, OI 30%, IV 10%) —
a ranking/quality metric, not a statistical estimate of how often the trade
actually wins. The real accuracy number — historical win rate — only comes
from `backtest_report.py` (run without `--mock` once you're on live data)
and from the paper-trading log over time. Don't conflate the two.

## Modes

| Command | What it does |
|---|---|
| `--mode watchlist` | One-shot scan, prints/Telegrams whatever qualifies right now, exits |
| `--mode paper` | Same, but also opens paper positions (single snapshot, no ongoing tracking) |
| `--mode monitor` | **Runs 24/7** (scanning during 09:00–15:30, idling the rest of the time), re-scanning every few minutes, alerting only on genuinely NEW setups (not re-announcing the same one every cycle). Stop it yourself with Ctrl+C (or `./stop_bot.sh`). Add `--paper` to also track paper positions live with real mark-to-market exits. |
| `--mode backtest` | Historical validation of the momentum signal |

```bash
# The one you probably want for daily use:
python3 main.py --mode monitor --paper --broker angelone
```

Signals below `config.MIN_SIGNAL_SCORE` (default 70) are dropped entirely
rather than force-filled to hit a count — some scans, and some whole days,
will legitimately return nothing. That's the screener working correctly,
not a bug.

### What `--mode monitor --paper` actually does, end to end

1. Scans every `config.MONITOR_INTERVAL_SECONDS` (3 min) during market hours
   (09:00–15:30). Outside that window it just idles — the process itself
   keeps running 24/7 so it can always answer Telegram, it just isn't
   hitting the broker API when the market's shut.
2. **Watchlist alert first, if applicable:** a symbol with real directional
   bias and a score in the `WATCHLIST_MIN_SCORE`–`MIN_SIGNAL_SCORE` range
   gets a lighter-weight "👀 SYMBOL - Building Bullish/Bearish Momentum"
   message — about the **underlying stock only** (spot price, score, why),
   deliberately with no strike/premium, since that depends on wherever the
   price actually is if/when a real signal fires later. Two conditions gate
   it:
   - **Strengthening, not just present:** score has to be higher than the
     last time that symbol+direction was seen (tracked in memory, reset
     daily) — sitting flat somewhere in the band isn't enough. A symbol's
     very first appearance in the band never fires either (no prior score
     to compare against yet) — it just seeds the baseline for next scan.
   - **Once per stock per day:** once a symbol+direction has fired a
     watchlist alert today, it won't fire again today even if it keeps
     climbing — one heads-up per stock per day, not one per scan cycle.

   `config.WATCHLIST_MAX_PER_SCAN` (default 3, highest score first) is a
   secondary safety net for the rare cycle where several *different* stocks
   strengthen at once — anything bumped by the cap isn't blocked for the
   day, just reconsidered next scan. Since scans repeat every few minutes,
   something appearing here can (not always) cross into a full signal a
   cycle or two later — a natural heads-up, not a guaranteed or
   precisely-timed one. A sharp move can also skip this tier entirely and
   go straight to a full signal between two scans, so "no watchlist alert"
   doesn't rule out a signal showing up next.
3. **Buy signal:** the moment a NEW setup clears the score floor, you get
   one Telegram message: entry, T1/T2/T3, stoploss, score, an estimated
   success probability (heuristic, not backtested — see below), and why it
   fired. The same setup won't be re-announced every cycle.
4. As the (paper) position's premium actually moves, you get a **separate
   message the instant T1/T2/T3/stoploss is hit** — with the exit price and
   P&L for that fill. Partial exits (e.g. T1 hit, 60% of the position still
   running) say so explicitly.
5. **~15 min before the 15:15 square-off** (`config.EOD_WARNING_MINUTES_BEFORE_CLOSE`),
   any still-open position gets an "⏳ End-of-day check-in" message showing
   current P&L if closed right now. Do nothing and it auto-squares-off at
   15:15 as usual, or reply **`hold SYMBOL`** to carry that specific
   position overnight yourself (profit/loss from there is on you). Holding
   is a same-day-only override — it resets back to auto-exit the next
   trading day, so you have to explicitly hold again if you want to keep
   carrying it.
6. If you exit a held (or any) position yourself outside the bot (e.g. via
   your broker's app), tell the bot **`close SYMBOL`** (or `close SYMBOL 250`
   with an explicit price) so its trade-room count and P&L summary stay
   accurate instead of showing a stale open position forever.
7. **Telegram commands, anytime:**
   - `status` — what the bot's doing right now (scanning / idle / outside
     market hours).
   - `summary` — today's recap: watchlist alerts, signals sent, fills/exits,
     running P&L. Resets at midnight; stops answering with today's data
     after 23:58.
   - `hold SYMBOL` / `close SYMBOL [price]` — see 5 and 6 above.

## What's in here

| File | Purpose |
|---|---|
| `config.py` | Universe, thresholds, T1/T2/T3/SL %, Telegram settings — tune here |
| `data_fetcher.py` | NSE public option-chain + historical data, with a synthetic mock fallback |
| `angelone_provider.py` | Live data via Angel One SmartAPI — use this instead of NSE scraping, which gets blocked |
| `diagnose_angelone.py` | Run this FIRST to confirm login + response field names before trusting live signals |
| `indicators.py` | RSI, EMA, MACD, ATR, IV percentile |
| `triggers.py` | Named technical triggers (breakout, breakdown, bounce, rejection, EMA cross, candle confirmation) |
| `screener.py` | Scores/ranks the universe into a shortlist, tags which triggers fired |
| `strategy.py` | Picks strike/expiry, sets entry/T1/T2/T3/SL, position sizing |
| `paper_trader.py` | Virtual order book with partial exits at T1/T2/T3 + CSV log (no real orders) |
| `telegram_notifier.py` | Sends a formatted alert to your Telegram when a signal fires |
| `backtest.py` | Approximate historical validation of the momentum signal (summary + detailed trade records) |
| `backtest_report.py` | Builds `backtest_report.html` — an interactive chart you open in a browser to SEE past signals plotted on the price history |
| `main.py` | CLI entry point: `--mode watchlist / paper / backtest` |

---

## Test it yourself, right now (step by step)

You don't need a broker account or the internet for this first pass — it
runs on synthetic data so you can see exactly what the bot does end-to-end.

**1. Open Terminal** (Spotlight search → type "Terminal" → Enter).

**2. Check Python is installed:**
```bash
python3 --version
```
If you see `command not found`, install Python from https://python.org/downloads
(get the macOS installer, run it, then reopen Terminal).

**3. Go to the project folder:**
```bash
cd ~/Downloads/"Claude Projects"/"Trading Bot"
```

**4. Install the three dependencies:**
```bash
pip3 install -r requirements.txt
```

**5. Run the watchlist scan (synthetic data, so it works right now):**
```bash
python3 main.py --mode watchlist --mock
```
You'll see a ranked list like:
```
HDFCBANK  CE  1950  entry 17.33  T1 19.93  T2 22.53  T3 25.99  sl 14.21
   why: Bullish bias -- EMA9 above EMA20; two consecutive bullish candle
   closes; volume 1.7x the 20-day average; options OI shows long buildup.
```

**6. Run the paper trading simulator:**
```bash
python3 main.py --mode paper --mock
```
This opens virtual positions and shows the booking plan (how many lots get
sold at T1/T2/T3). Check `paper_trades.csv` afterward — that's your fill log.
Each row logs the exit (level, price, P&L, timestamp) **plus** the entry
context that produced it (entry price, score, estimated win probability, and
the semicolon-joined technical reasons) — so after a few weeks of real data
you can analyze not just your overall win rate, but which score bands or
trigger reasons actually performed best. If you already have an older
`paper_trades.csv` from before this field was added, don't worry — the bot
detects the column mismatch automatically, archives your old file as
`paper_trades.csv.pre_DATE.bak` (nothing is lost), and starts a fresh log
with the new columns.

**7. Generate the visual backtest dashboard (this is the "see it with your
own eyes" part):**
```bash
python3 backtest_report.py --mock
```
Then open `backtest_report.html` (double-click it in Finder, or run
`open backtest_report.html`). You'll get:
- A dropdown to pick any stock/index
- Its price chart with **▲ triangles marking every past buy signal** and
  **● dots marking the exit** (green = win, red = loss)
- KPI cards: total signals, win rate %, average return per trade, best/worst
  performing symbol
- A sortable table of every historical signal with its exact reason

This is with **synthetic data**, so the win rate you see is just proof the
plumbing works — not a real edge. Once you swap in real NSE/broker data
(step below), re-run the same command and the dashboard becomes a genuine
report card on the strategy.

**8. Live data:** the free NSE route (`--broker nse`, the default) turned out
to be blocked by NSE's anti-bot layer even from a normal home connection —
that's a known, common problem with this unofficial API, not something
specific to your setup. The reliable path is Angel One SmartAPI instead
(free, see the next section):
```bash
python3 main.py --mode watchlist --broker angelone
python3 backtest_report.py --broker angelone
```

---

## Telegram alerts — step by step

**1. Create the bot.** Open Telegram, search for **`@BotFather`**, start a
chat, send `/newbot`. Follow the prompts (give it a name and a username
ending in `bot`). BotFather replies with a token that looks like
`123456789:AAExampleTokenTextHere` — copy it.

**2. Get your chat ID.** Send any message (e.g. "hi") to your new bot from
your Telegram account. Then, in a browser, visit:
```
https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
```
(replace `<YOUR_TOKEN>` with the token from step 1). Look for
`"chat":{"id":123456789,...}` in the response — that number is your chat ID.

**3. Fill in `config.py`:**
```python
TELEGRAM_ENABLED = True
TELEGRAM_BOT_TOKEN = "123456789:AAExampleTokenTextHere"
TELEGRAM_CHAT_ID = "123456789"
```
(Or set environment variables `TB_TELEGRAM_TOKEN` / `TB_TELEGRAM_CHAT_ID`
instead, if you'd rather not save the token in a plain file.)

**4. Send a test message:**
```bash
python3 telegram_notifier.py
```
You should get a message in Telegram confirming it's wired up.

**5. From then on**, every `python3 main.py --mode watchlist` (or `paper`)
run will push each signal to Telegram automatically, formatted with entry,
T1/T2/T3, stoploss, score, and the exact reason it fired.

---

## Angel One SmartAPI setup — step by step

This replaces the free NSE scraping route, which gets blocked by anti-bot
protection even from normal home connections.

**1. Account.** If you don't already have an Angel One trading account, open
one free at angelone.in (digital KYC — PAN, Aadhaar, bank details).

**2. Create an API app.** Go to smartapi.angelone.in, log in, create a new
app (any name). This gives you an **API Key**.

**3. Enable TOTP.** Go to smartapi.angelone.in/enable-totp, log in with your
client code + MPIN, verify the OTP sent to your email/phone, then scan the
QR code with Google Authenticator — note the **secret key/token** string
shown next to the QR code (not the rotating 6-digit code itself).

**4. You now have four values**: API Key, Client Code (e.g. `A123456`),
your trading PIN, and the TOTP secret.

**5. Set them as environment variables** — do not put these in `config.py`
or send them to Claude. Unlike the Telegram token, this combination can log
into your real trading account:
```bash
nano ~/.zshrc
```
Add at the bottom:
```bash
export ANGELONE_API_KEY="your_api_key"
export ANGELONE_CLIENT_CODE="your_client_code"
export ANGELONE_PIN="your_pin"
export ANGELONE_TOTP_SECRET="your_totp_secret"
```
Save (`Ctrl+O`, Enter, `Ctrl+X`), then `source ~/.zshrc` (or open a new tab).

**6. Install the SDK:**
```bash
pip3 install smartapi-python pyotp logzero websocket-client
```

**7. Run the diagnostic script FIRST** — this confirms login works and
shows the raw response shapes before the real bot trusts them:
```bash
python3 diagnose_angelone.py RELIANCE
python3 diagnose_angelone.py NIFTY
```

**Status: verified working end-to-end** (2026-07-06) against a live account
for both stocks and indices — real signals generated for HDFCBANK,
ICICIBANK, NIFTY, BANKNIFTY, FINNIFTY etc., delivered to Telegram
correctly. Getting here caught and fixed a few real bugs along the way
(wrong exchange segment mixing NSE/BSE options, a naive string sort that
picked the wrong expiry, a backwards field assumption for index names, and
an API token-limit that needed request chunking) — all documented in
`angelone_provider.py`'s comments if you want the details.

**8. Use `--broker angelone` on any command:**
```bash
python3 main.py --mode watchlist --broker angelone
python3 main.py --mode paper --broker angelone
python3 backtest_report.py --broker angelone
```

**One known gap:** OI-change (vs. flat OI) isn't directly available from
SmartAPI, so `angelone_provider.py` tracks OI snapshots itself in a local
file to compute the change since this morning — a reasonable stand-in, but
it only becomes meaningful after the bot's been running that day for a
while (the very first reading of the day has nothing to compare against
yet). IV comes cleanly from Angel One's `optionGreek` endpoint, confirmed
working.

**Also worth knowing:** `config.py`'s `STOCK_UNIVERSE` list needs occasional
manual upkeep — NSE's F&O list changes quarterly, and corporate actions
(mergers, demergers, symbol changes) can make an old symbol stop resolving,
which is what happened with TATAMOTORS (removed for now, see the comment
in `config.py`).

---

## The 8am problem

NSE cash/F&O trades 09:15–15:30 IST (pre-open 09:00–09:08). **There is no
live option-chain data at 8am** — the exchange is closed, so any "signal" at
that hour can only be based on yesterday's close plus overnight cues (GIFT
Nifty, US market close, crude, dollar index). Recommended split (already in
`config.py`):

- **08:00 — pre-market prep**: run the screener on prior-day data to build a
  watchlist of stocks worth watching at the open.
- **09:20 — live signal run**: once real LTP/OI is flowing, confirm bias on
  the watchlist and emit the actual entry/T1/T2/T3/SL, and push to Telegram.

I can wire up a scheduled task for the 8am prep step right now if you want —
just say so. The 9:20 live run needs to execute on a machine with a live
data feed, so it's better run via your own OS scheduler (cron / Task
Scheduler) than inside a chat session.

## Swapping in a broker feed (Upstox / Angel One / Zerodha)

`data_fetcher.py` is intentionally isolated: everything downstream only
depends on the `UnderlyingSnapshot` / `OptionQuote` shapes, not on NSE
specifically. To move to a broker feed:

1. Sign up for API access (Upstox API and Angel One SmartAPI are both free;
   Zerodha Kite Connect charges ~₹2000/month).
2. Write a new class in `data_fetcher.py`, e.g. `UpstoxDataProvider`, that
   implements `get_underlying_snapshot(symbol, is_index)` and returns the
   same dataclasses.
3. Point `get_provider()` at it.

I can build that provider class once you've picked a broker and generated
API credentials.

## Important: 2026 SEBI/NSE algo trading rules

NSE rolled out mandatory retail-algo compliance from **1 April 2026**
(source: Angel One, citing NSE circular). This matters once you move past
paper trading to real order placement:

- Orders from self-coded algos must originate from a **registered static
  IP** (a "tech-savvy client" running their own logic can self-host and
  register their own static IP — this is the path this bot is built for).
- Algorithms below 10 orders/second don't need individual exchange
  registration; above that threshold, they do.
- **Market and IOC order types are prohibited** for algo trading — use
  LIMIT orders only.
- Non-self-hosted / broker-hosted retail algos may require participation in
  monthly mock trading sessions.

None of this affects the watchlist/paper-trading/Telegram-alert phase
you're in now — it only applies once real orders start hitting the exchange.

## Running 24/7, even when your Mac is off

`start_bot.sh` / `stop_bot.sh` (see repo root) run the bot in the background
so closing the Terminal window doesn't kill it — but it still dies if your
Mac is fully shut down, asleep, or restarted. To have it survive that, it
needs to run on a separate always-on machine:

- **Free option (recommended for testing):** see **ORACLE_FREE_DEPLOY.md** -
  Oracle Cloud's Always Free tier, a real server at zero cost, indefinitely
  (uses `tradingbot-oracle.service`).
- **Paid option (~$4-6/mo):** see **CLOUD_DEPLOY.md** - a Hostinger/
  DigitalOcean VPS, once you're ready to commit (uses `tradingbot.service`).

## Moving this to a different computer

See **RUN_ON_ANOTHER_DEVICE.md** for copying the whole setup to a different
Mac, Windows PC (via Command Prompt - includes `start_bot.bat`/`stop_bot.bat`),
or Linux machine.

## Version control (GitHub)

See **GITHUB_SETUP.md** for putting this project on GitHub so you can
`git clone`/`git pull` it onto any device instead of manually copying files.
As of this setup, credentials (Angel One AND Telegram) are environment-variable-only -
nothing sensitive lives in the code, so it's safe to version control.

## Suggested path from here

1. Run `backtest_report.py` and `--mode watchlist` daily for a couple of
   weeks with real (non-mock) data once you're on a normal network.
2. Run `--mode paper` daily, and loop `mark_to_market()` during market hours
   so partial exits get simulated against real LTP — review
   `paper_trades.csv` weekly for real win-rate/drawdown, and re-run
   `backtest_report.py` periodically to watch the accuracy trend visually.
3. Only after paper results look genuinely good (and you understand *why*,
   not just that the number is green) — connect a broker order-placement
   call, respecting the static-IP/order-type rules above.
4. I do not place or execute real trades — that step is always yours.
