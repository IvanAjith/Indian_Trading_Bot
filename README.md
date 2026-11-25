# NSE F&O Intraday Options Signal Bot (paper-trading stage)

A rules-based bot that watches NSE index and stock options during market hours, sends a
**Telegram alert** when a setup lines up (entry, three targets, stoploss, lot size, and the reasons),
and **paper-trades** every signal with a virtual ₹1,00,000 account so you can measure whether the
rules really work before risking money.

- Runs **free on GitHub Actions** - no server, no laptop. One scheduled session each trading day.
- **Never places real orders.** Alerts are for you to act on by hand; results are simulated.
- The "score" is how many technical conditions lined up, **not** a win probability. The only real
  accuracy number is the paper-trade log (`paper_trades.csv`) after weeks of sessions.

---

## What happens each trading day

| Time (IST) | What the bot does |
|---|---|
| **20:00 (evening before)** | `Market holiday notice` job messages you only if tomorrow is a weekday NSE holiday |
| **~09:15** | Session starts, logs in to Angel One, sends **🟢 Bot started** |
| **09:15 - 09:20** | Waits, then confirms via live data that NSE actually traded today. If not (holiday) it sends **📅 Market closed today** and stops |
| **09:20 - 15:05** | Scans continuously. Sends heads-ups, buy signals, Target 1 / exit alerts; paper-trades each signal |
| **~14:50** | End-of-day check-in for any position still open |
| **15:05** | Any open paper position is squared off (intraday only) |
| **15:10** | Session ends and sends the **daily summary** (signals, exits, P&L, bot health) |

If you get no "Bot started" message by ~09:45 on a trading day, something is wrong - open the repo's
**Actions** tab. GitHub also emails you when a run fails, and the bot sends **❌ Run FAILED**.

---

## What the bot scans

- **NIFTY, BANKNIFTY, FINNIFTY**, plus
- each morning it reads Angel One's live F&O contract list, ranks stocks by how much money is open in
  their near-the-money options (open-interest value) and scans the **top 80**, plus your curated list in
  `config.py` (if those still have live options). About 85-90 symbols in total.
- Per symbol: the nearest expiry, at-the-money strike ±3 (7 CE and 7 PE contracts).
- Prices are fetched ~40 per API call, so a full scan takes roughly a minute (an estimate - the real
  figure is in the run's `monitor.log`). It is **polling, not live streaming**: a spike between two scans
  can be missed.

## How a signal is decided

Each symbol gets a 0-100 **score**:

| Component | Weight | Meaning |
|---|---|---|
| Momentum | 40% | RSI > 55 with EMA9 above EMA20 → bullish; RSI < 45 with EMA9 below EMA20 → bearish |
| Option open interest | 30% | OI buildup that matches the price direction |
| Volume | 20% | Today's volume vs the 20-day average |
| IV | 10% | Off by default (`FETCH_IV`), so effectively a constant |

- Score **≥ 70** → full **buy signal** (up to `TOP_N_SIGNALS_PER_DAY` = 5 new ones per scan, best first) (call for bullish, put for bearish).
- Score **60-70 and rising** → one **👀 early heads-up** per stock per day.
- Named triggers explain *why* it fired (20-day breakout/breakdown, support bounce/rejection, EMA trend,
  two confirming candles, volume spike, OI buildup).

## The trade plan (paper account)

- **Entry:** at-the-money option (nearest expiry). Paper fills assume **1% slippage** each side.
- **Stoploss:** −18% on the option price. **Targets:** +15% (T1), +30% (T2), +50% (T3); sell 40% / 30% / 30%.
- **At Target 1:** you get an alert to **move your stoploss to cost (cost-to-cost)**; the paper account
  does the same.
- **One active trade per stock/index.** While a trade on, say, NIFTY is still running (target / stoploss
  not finished) the bot sends **no further NIFTY alert or heads-up at all** - same direction at a new
  strike, *or* the opposite direction (no panic-reversals: you wait for the trade to hit target or stoploss).
  After it closes there is a 30-minute cooldown (`REENTRY_COOLDOWN_MIN`) before NIFTY can alert again.
- **Every alert is paper-traded** (`PAPER_TRACK_ALL_SIGNALS`), so `/trades` and `paper_trades.csv` match
  your alerts one-to-one. No cap on open positions.
- **Position size:** real NSE **lot size** from Angel One's contract list. Lots are chosen so the loss at
  the stoploss is ≤ **5% of capital** and the premium is affordable. If even one lot is too big, it is
  still tracked as **1 lot** and marked ⚠️ oversize (so you can still judge the signal). A one-lot trade
  can't be split, so it exits fully at T2.
- Everything is squared off at **15:05**.

---

## Telegram messages

**Entry signal** - long message, starts with 🚨 (this is the one to act on)
```
🚨🚨 ENTRY ALERT - BUY NOW 🚨🚨
🟢 BUY RELIANCE 29 DEC 1400 CE at ₹32.00
⏰ 9:48 AM
🎯 Target 1: ₹36.80   🎯 Target 2: ₹41.60   🎯 Target 3: ₹48.00
🛑 Stoploss: ₹26.24
📦 Lot size 500 | 1 lot costs ₹16,000 | max loss at SL ≈ ₹2,880 per lot
📊 Score: 74/100
💡 Why: broke above 20-day resistance; EMA9 above EMA20; volume 1.8x average; options OI shows long buildup
```

**Watchlist heads-up** - deliberately tiny, so you can tell it apart at a glance
```
👀 WATCH ONLY: RELIANCE ↑ (bullish, score 66)
Add to watchlist - not an entry yet.
```

**Target 1 reached**
```
🎯 Target 1 reached - RELIANCE 29 DEC 1400 CE
Price ₹36.80 (+15% from entry)
🔒 Move your stoploss to cost (cost-to-cost): ₹32.32
```

Other automatic messages: 👀 early heads-up · 🔚 exit (T2/T3/stoploss/time) with P&L · ⏳ end-of-day check-in
· ⚠️ data feed down / ✅ recovered · 📅 market closed today · 🏖️ market closed tomorrow (8 PM) ·
❌ bot could not start / run failed · 🔚 session ended with the daily summary.

### Commands you can send (with or without `/`; answered only while a session is running)

| Command | Reply |
|---|---|
| `/help` | The command list |
| `/status` | Whether the bot is scanning, last scan time, signals sent |
| `/trades` | All paper trades today: open ones with live P&L, stoploss and targets, closed ones with result and P&L |
| `/pnl` | Booked + open profit and loss, win rate, paper capital |
| `/summary` | Today's heads-ups, signals, exits and P&L so far |
| `/add SYMBOL STRIKE CE\|PE ENTRY [LOTS]` | Track a trade you took yourself, e.g. `/add NIFTY 25800 CE 249.87`. Uses your entry price, the real lot size, the usual stoploss/targets, T1 alert and exits |
| `/close SYMBOL [price]` | Record a position as closed (at the latest price, or yours) |

---

## Setup (one time)

1. **Add six repo secrets:** Settings → Secrets and variables → Actions → New repository secret.
   Type them only into GitHub - never paste them into chat or code.

   | Secret | Where to find it |
   |---|---|
   | `TB_TELEGRAM_TOKEN` | Telegram → **@BotFather** → `/mybots` → your bot → API Token (or `/newbot`) |
   | `TB_TELEGRAM_CHAT_ID` | Send your bot a message, open `https://api.telegram.org/bot<TOKEN>/getUpdates`, copy `"chat":{"id": ...}` |
   | `ANGELONE_CLIENT_CODE` | Your Angel One login ID (e.g. `A123456`) |
   | `ANGELONE_PIN` | Your 4-digit trading MPIN |
   | `ANGELONE_API_KEY` | smartapi.angelone.in → My Apps (or Create App) |
   | `ANGELONE_TOTP_SECRET` | smartapi.angelone.in/enable-totp → the long secret text next to the QR code (**not** the 6-digit code) |

2. **Test run:** Actions tab → **NSE options signal bot** → Run workflow. Enter an end time a few
   minutes ahead on a **24-hour clock** (e.g. `22:45` for 10:45 PM). You should get "Bot started" then
   "Session ended".
3. **Holiday notice test:** Actions → **Market holiday notice** → Run workflow with `today` set to the day
   before a holiday (e.g. `2026-12-24`).
4. Make sure no other copy of the bot is running anywhere with the same Telegram token.

### Optional repo variables (Settings → Secrets and variables → Actions → Variables)

| Variable | Default | Use |
|---|---|---|
| `SESSION_END` | `15:10` | When the session ends (24-hour IST) |
| `JOB_TIMEOUT_MIN` | `360` | Safety stop for the job |

A **public** repo has unlimited free Actions minutes (the default full day is fine). On a **private** repo
(2,000 free minutes/month) set `SESSION_END=10:30` and `JOB_TIMEOUT_MIN=100`. Being public makes the code,
run logs and downloadable artifacts visible to everyone; secrets stay hidden.

### Holidays

The morning check uses live data, so it can never block a real trading day. The 8 PM notice reads NSE's
official list and falls back to `market_holidays.json`: add weekday holidays as `"YYYY-MM-DD": "Reason"`
(copy from nseindia.com → Resources → Exchange Communication → Holidays) and refresh it each December.

---

### Held trades are always priced
A stock/index with an open trade is always scanned, and its exact contract is always fetched - even if the
price has moved far from the at-the-money strike or the stock dropped out of the top-80 ranking - so targets,
stoploss and exits keep working. (Earlier versions could silently lose the price of a trade once the market
moved away from its strike.)

## Reviewing results

Each run uploads `monitor.log` and `paper_trades.csv` (Artifacts section of the run page, kept 90 days).
Every `paper_trades.csv` row is one fill: symbol, strike, level (T1/T2/T3/SL/TIME/MANUAL), lots, price,
P&L, entry price, score, lot size and the trigger reasons - so you can analyse which score bands and
reasons actually win. Review weekly; judge the rules on weeks of data, not one day.

Optional visual backtest (approximate - uses daily candles and a fixed-delta premium model):
`python3 backtest_report.py --broker angelone` → open `backtest_report.html`.

## Settings (`config.py`)

| Setting | Default | What it does |
|---|---|---|
| `MIN_SIGNAL_SCORE` / `WATCHLIST_MIN_SCORE` | 70 / 60 | How strict signals / heads-ups are |
| `STOPLOSS_PCT`, `TARGET_PCT_T1/T2/T3` | 18, 15/30/50 | Stop and targets on the option price |
| `MOVE_SL_TO_COST_AFTER_T1` | True | Move the stop to entry once T1 is reached |
| `ONE_ACTIVE_PER_UNDERLYING`, `REENTRY_COOLDOWN_MIN` | True, 30 | No other alert (either direction) on a stock/index while its trade is open; wait N min after it closes |
| `PAPER_TRACK_ALL_SIGNALS` | True | Paper-trade every alert (no position cap; oversize lots tracked as 1 lot) |
| `MAX_CAPITAL_PER_TRADE_PCT`, `MAX_TRADES_PER_DAY` | 5, 3 | Risk per trade / max open positions (the cap applies only if tracking-all is False) |
| `PAPER_SLIPPAGE_PCT`, `PAPER_STARTING_CAPITAL` | 1.0, 1,00,000 | Paper-trading realism |
| `FORCE_EXIT_TIME` | 15:05 | Intraday square-off time |
| `AUTO_UNIVERSE`, `AUTO_UNIVERSE_MAX_STOCKS`, `STOCK_UNIVERSE` | True, 80 | What is scanned (more = slower scans) |
| `FETCH_IV` | False | Extra IV call per symbol (a near-constant score input) |

## Files

| File | Purpose |
|---|---|
| `.github/workflows/bot.yml` | The daily session (weekdays, 09:15 IST) |
| `.github/workflows/holiday-notice.yml` | 20:00 IST "market closed tomorrow" notice |
| `main.py` | Orchestrator: scan loop, alerts, Telegram commands, session start/end |
| `angelone_provider.py` | Angel One data feed (batched quotes, lot sizes, auto universe, market-open check) |
| `data_fetcher.py` | Shared data shapes plus a mock provider for offline testing |
| `screener.py`, `triggers.py`, `indicators.py` | Scoring, named triggers, RSI/EMA/ATR |
| `strategy.py` | Strike, entry/targets/stoploss, lot-aware position sizing |
| `paper_trader.py` | Virtual account: slippage, partial exits, stoploss-to-cost, CSV log |
| `telegram_notifier.py` | Message formats and Telegram sending/polling |
| `market_calendar.py`, `holiday_notice.py`, `market_holidays.json` | Holiday lookup and notice |
| `backtest.py`, `backtest_report.py` | Approximate historical check and HTML report |
| `diagnose_angelone.py` | Prints raw Angel One responses to debug login/data |
| `config.py` | All settings |
| `GITHUB_ACTIONS_DEPLOY.md` | Deployment and health-check details |

## Known limitations

- Signals use **daily** RSI/EMA plus the live price - no intraday candles or VWAP yet.
- The first 5 minutes after the open are ignored (waiting for the market-open check).
- OI change is measured against the bot's first reading of the day, so it is weak early on.
- Polling, not streaming; real option spreads can exceed the 1% slippage assumed in paper fills.
- Angel One may restrict API access by IP; GitHub's servers change IP each run. If login fails you get
  "Bot could not start".
- The paper results are the only evidence of an edge. Do not trade real money from these alerts until
  weeks of results justify it, and note SEBI/NSE rules for algorithmic orders (registered static IP,
  limit orders only) would apply before any automation of real orders.

## Run locally (optional, for development)

```bash
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
python3 main.py --mode monitor --paper --mock --max-cycles 3     # synthetic data
python3 main.py --mode monitor --paper --broker angelone         # live data (needs the six env vars)
```
`start_bot.sh` / `stop_bot.sh` run that live command in the background. `.env.example` lists the variable names.
