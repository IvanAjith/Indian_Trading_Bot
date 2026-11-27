# Future plan and upgrade backlog

Written 25 Nov 2026. This is the single place that lists **what is settled, what to review, and what to build next**, with
the concrete actions for each item, so any later session can pick up from here.

**How to use this file:** at each review checkpoint, go through section 3; choose items from section 4; then ask for them
one at a time ("build item 4.2"). Keep one change per release, so results stay comparable.

---

## 1. Decisions already made (do not re-debate without data)

| Topic | Decision |
|---|---|
| Mode | Paper trading only. The bot never places real orders. |
| Run | GitHub Actions, weekdays, start ~09:15 IST (backup starts 09:20/09:25/09:30), end 15:12. Repo is public. |
| Entry alert | Score **70 or more** (`ALERT_SCORE`). Best score first, 2 per scan, no daily limit. |
| Heads-up | Score **65 to 69.9**, rising, once per stock per day (`WATCHLIST_MIN_SCORE`). |
| Silent tracking | Signals 65-69.9 are paper-traded silently and journaled (`SHADOW_TRACK_LOWER_BAND`). |
| Budget | Skip a signal if **one lot costs more than ₹35,000** (`MAX_COST_PER_LOT`). |
| Repeat alerts | **Stocks: one alert per stock per day** - no repeat, whether it ended in profit or loss. **Indexes** (NIFTY, BANKNIFTY, FINNIFTY) may repeat: the earlier call must be **closed**, **30 minutes** must have passed since it closed, the normal 70+ score is enough, and at most **3 alerts per index per day**; after a profit the new call must be at a **different strike** from all that day's profitable calls, after a loss the same strike is allowed. CE and PE. (Changed 26 Nov 2026 evening.) |
| Volume | Live traded volume from the quote on every scan (fixed 26 Nov 2026: a 4-hour cached candle had frozen the volume, so morning scores were too low and alerts burst when it refreshed). Optional time-of-day volume ratio: `VOLUME_TIME_ADJUST`, off. |
| Score | 40% momentum, 20% live volume, 30% OI, 10% IV, plus (from 26 Nov evening): OI counts only when it agrees with the trade; stretched RSI (stock call 70+/put 30-) -5; continuation (2%+ same-direction move + agreeing OI + not stretched) +6. Built from ONE day of data - re-check at the 11 and 18 Dec checkpoints (weekly review factors: oi_against, stretched, continuation). |
| Intraday trend | The alert's 'bullish/bearish trend' is the DAILY EMA9/EMA20 of the stock (wording now says 'daily'). From 27 Nov each alert shows the stock's 15-minute trend (EMA9 vs EMA20, price vs VWAP) as information and the journal records it (`intraday`); lab candidate `intraday_15m_must_agree` tests it as a gate. No score change. |
| Learning loop | Live rules are FIXED for the week (`RULES_VERSION`). Every night `rule_lab.py` replays candidate rules on the journal (out-of-sample days only count; in-sample is shown but not trusted), shows the trade-off, runs data checks and sends a short Telegram note. The weekly meeting approves AT MOST ONE change, then `RULES_VERSION` is bumped. Silent tracking floor lowered to 60 (heads-ups still 65+; max 40 silent trades/day). Candidates: rsi_cap_65, stretched_penalty_10, continuation_bonus_10, oi_must_agree, move_1pct_with_trade, window_0950_1430, day_move_not_against, volume_credit_only_if_move_agrees, intraday_15m_must_agree. (26 Nov 2026) |
| Scan speed | Two-stage scan (option chains only for symbols that can still reach the heads-up band; first scan of the day and every 20th are full), daily history pre-loaded before the open, history cached for the whole session. Target: a scan in ~30 s instead of ~90 s, so heads-ups/alerts arrive closer to the moment a score crosses its threshold. Silent tracking stops at 14:55. (26 Nov 2026) |
| Reasoned strikes | ATM is always tried first. If ATM costs over the budget, the bot looks along the chain (at most 2 strikes from ATM, delta at least 60% of ATM's, never just to fit the budget) and picks a strike by reasons (cost, delta, reachable move, liquidity, OI wall; closest to the money that passes) and **sends a real alert** at it (`CHEAPER_STRIKE_ALERTS` ON from 26 Nov 2026 evening, with a 'not tested yet' note); if nothing passes it is skipped and tracked silently. Tagged in the journal; the weekly review compares 'Strike type'. Open question for the checkpoints: rank by measured reward per rupee instead of 'closest to ATM'. |
| Plan | **Indexes** (NIFTY, BANKNIFTY, FINNIFTY): 3 lots, 3 targets. **Stocks**: 2 lots, 2 targets. One lot sold at each target. |
| Targets/stop | T1 +15%, T2 +30%, T3 +50% (indexes only); stoploss -18%. After T1 stop → cost; after T2 (indexes) stop → T1 price. |
| Square-off | 15:10 (`FORCE_EXIT_TIME`); nothing carried overnight. Session ends 15:12 (GitHub's 6-hour job limit). |
| Late signals | **No alerts or heads-ups after 14:45** (`NO_ALERTS_AFTER`); later signals are tracked silently. Added 25 Nov 2026 after the old session sent alerts at 14:52 and 15:09 (last 5 late trades lost ₹10,071 vs +₹27,948 for the 10 earlier ones - one day only). |
| Messages | Short, action-first, every amount **per lot**. Watch message names the contract. Entry alert has no "plan" section. |
| Paper fills | 1% slippage each side; charges not deducted (summaries say "before brokerage and taxes"). |
| End of day | Summary shows each trade, day total (capital invested → returned → profit/loss), Plan A (full plan) vs Plan B (1 lot sold at T1), peak capital, silent-tracking line. |
| Journal | Every closed trade is written up and saved on the `journal-data` branch; Friday 17:00 IST weekly review → Telegram. It **recommends**; a person approves. |
| Open-trade checks | Between full scans the bot re-prices open trades every 20 s (`QUICK_CHECK_SECONDS`, one small quote call), so T1/T2/T3/stoploss messages arrive within ~20-30 s instead of after a whole scan (added 26 Nov 2026 after a late T1 message). |
| Strikes | At-the-money for now. Not included: SENSEX, MIDCPNIFTY, commodities. |

**Freeze:** keep these settings unchanged for **2-3 weeks from Friday 27 Nov 2026** so the data is comparable. (26 Nov is not counted: a stale-volume bug distorted its scores; it is fixed in the 26 Nov evening release.)

---

## 2. Checkpoints

| Date (Fri) | What |
|---|---|
| Fri 27 Nov | First weekly review (1 trading day - only checks that the plumbing and the new rules work). |
| Fri 4 Dec | First useful review (6 trading days). |
| **Fri 11 Dec** | **2-week checkpoint** (11 trading days) - go through section 3. |
| **Fri 18 Dec** | **3-week checkpoint** (16 trading days) - decide the first upgrades from section 4. |

Day one of the clean record is **Friday 27 Nov 2026** (25 Nov: different rules and a mid-day restart; 26 Nov: stale-volume bug). Summaries and reviews show profit/loss in rupees **and as a percentage** (return on the money invested, and as % of the ₹1 lakh account).

Where the data lives: `journal-data` branch → `journal/YYYY-MM-DD.md` (read), `journal/trades.jsonl` (analysis),
`journal/weekly/*.md` and `journal/learnings.json` (reviews); run artifacts → `trade_results.csv`, `paper_trades.csv`.

---

## 3. Review checklist (answer from the numbers)

0. **Rule lab:** open `journal/lab/candidates.md` - which candidate beats the live rules OUT-OF-SAMPLE with enough alerts (30+)? What is its trade-off (winners dropped)? Are the data checks clean? Approve at most one, then bump `RULES_VERSION`.

1. **Plumbing:** did "Bot started" arrive every trading day? Did every summary and journal entry arrive? Any failed run?
2. **Alert threshold:** alerted (70+) vs silent (65-69.9) win rate and average P&L. Close → lower `ALERT_SCORE`; clearly worse → keep.
   Also win rate by score band 70-74 / 75-79 / 80-89 / 90+ - is a higher floor justified?
3. **Warning factors:** which of weak volume, no OI confirmation, against NIFTY, chasing, opening, late entry, near threshold,
   quick stop really have a lower win rate (weekly review table)? Turn clear ones into rules (see 4.1).
4. **Entry time:** win rate for entries after 14:00 and in the first half hour. If poor → cutoff (4.4).
5. **Plans:** index (3 lots) and stock (2 lots): Plan A vs Plan B totals. Does holding for T2/T3 beat taking T1? Does the trail help?
6. **Capital:** "Peak capital in use" vs the ₹1 lakh account. Too high often → money-in-use cap (4.6) or fewer alerts.
7. **Realism:** compare a few paper entries/exits with real broker prices at that minute. Large gaps → realistic fills (4.3).
8. **Cost/instrument mix:** which instruments and cost-per-lot groups earn most? Are BANKNIFTY/FINNIFTY missing because of the ₹35,000 filter (→ 4.2)?
9. **Scan speed:** "First scan done in X min" and average scan time in the summary (26 Nov, before two-stage: first 10.4 min, avg 91 s, slowest 364 s, 275 retries); confirm the two-stage scan brought it to ~30 s and the first scan to ~1-2 min. Still too slow → lower `AUTO_UNIVERSE_MAX_STOCKS`.
10. **Charges:** estimate brokerage + taxes on the average trade vs the average profit (a ₹20 order fee and STT matter on small wins).

---

## 4. Backlog (effort: S small, M medium, L large)

### 4.1 Rules learned from the review - S/M - **after the 2-3 week review**
- **What:** convert accepted recommendations into filters, e.g. `MIN_VOLUME_RATIO`, `REQUIRE_NIFTY_AGREEMENT` (stocks), `REQUIRE_OI_CONFIRMATION`, a higher `ALERT_SCORE`.
- **Actions:** add the setting in `config.py`; apply it in `main.py` where signals are filtered (next to the budget check); mark skipped signals as silent so they keep being measured; re-run the weekly review to confirm.
- **Rule:** the bot never edits its own rules; a person approves each change.

### 4.2 Cheaper strikes (move along the option chain) - BUILT 26 Nov (silent test); alerts OFF until the journal supports it
- **What:** pick a strike 1-2 steps out of the money (higher strike for calls, lower for puts) to cut premium and capital; helps BANKNIFTY/FINNIFTY pass the ₹35,000 filter.
- **Trade-off:** needs a bigger index move for the same % target; cheap options die worthless more often; spreads are wider.
- **Actions:** (1) background comparison first - for each signal also paper-track the ATM, +1 and +2 strike contracts silently and journal them (`strategy._pick_strike`, silent trades); (2) review which wins; (3) then set `STRIKE_OFFSET` (already in `config.py`, 0 = at the money). The chain fetched is ATM ±3 strikes, so offsets up to 3 work without new data calls.

### 4.3 Realistic paper fills and charges - M
- **What:** enter at the real best ask and exit at the best bid (Angel One `getMarketData` FULL includes depth) instead of a flat 1%; deduct brokerage (₹20/order) and taxes (STT, exchange fees, GST, stamp duty) in the P&L.
- **Actions:** read `depth` in `angelone_provider._quotes_from_rows` (add bid/ask to `OptionQuote`); use them in `paper_trader.open_trade/_fill`; add a charges function (the formulas used in the 25 Nov analysis are a starting point - verify current rates); show "after charges" next to "before charges" in the summary.

### 4.4 Late-entry cutoff - DONE (14:45) - revisit at the checkpoints
- **Built:** `NO_ALERTS_AFTER = "14:45"` in `config.py`; the alert loop routes later signals to silent tracking and heads-ups stop.
- **Revisit:** the weekly review's Entry-time table (and the silent trades after 14:45) show whether 14:45 should move earlier/later.

### 4.5 NIFTY-trend filter - S/M - after review item 3
- **What:** skip (or silence) stock signals that oppose NIFTY's move on the day; the journal already records `nifty_chg_pct`.
- **Actions:** add `REQUIRE_NIFTY_AGREEMENT` and apply it in the alert loop; same threshold as `journal.AGAINST_MARKET_PCT`.

### 4.6 Money-in-use cap - M
- **What:** replace "no daily limit" with "no new alerts while open trades tie up more than ₹X" (e.g. ₹1 lakh), resuming as trades close.
- **Actions:** compute open capital from `account.open_positions` (entry × lot × lots remaining); gate the alert loop; message when blocked; keep strong/valuable signals configurable (e.g. score 90+ bypass).

### 4.7 MIDCPNIFTY (NSE) - S
- **What:** one more NSE index. **Actions:** add to `INDEX_UNIVERSE` in `config.py`; check its lot size, strike step and cost per lot with the diagnostic (`python diagnose_angelone.py MIDCPNIFTY`); paper-trade a week.

### 4.8 SENSEX (BSE) - M
- **Why not now:** SENSEX options trade on the **BSE** (Angel segment BFO); the data layer only handles NSE (`NFO`), and BFO contracts were deliberately excluded after an earlier bug.
- **Actions:** (1) extend `diagnose_angelone.py` to print SENSEX index token, BFO option contracts and quote shapes - run it on a real login; (2) in `angelone_provider.py` add BSE index lookup and BFO option indexing (`_build_indexes`, `_pick_near_atm`, `_market_data("BFO", ...)`); (3) holidays/hours are the same as NSE; (4) paper-trade silently for a week before alerts.

### 4.9 Commodities (MCX) - L - build last, behind a switch
- **Differences:** the underlying is a **futures contract** (crude oil, natural gas, gold, silver ...); trading runs ~09:00-23:30; separate holiday calendar; different lot sizes/price units; global news drivers.
- **Actions:** (1) diagnostic script for the MCX segment first (instrument types, futures tokens, option chains, quote fields); (2) a separate provider module + `MCX_UNIVERSE` setting, fully switch-off by default; (3) **two or three sessions per day** (GitHub caps a job at 6 hours), e.g. 09:00-14:30 and 17:00-23:20, with their own square-off; (4) MCX holiday handling; (5) its own score tuning; (6) paper-only, silent first.
- Decide which commodities first (crude oil, natural gas, gold, silver?).

### 4.10 Better signals - L
- Intraday 5-minute candles, VWAP, opening-range logic instead of daily RSI/EMA + live price (the signal currently looks similar all day).
- Better open-interest change (use exchange previous-day OI instead of "since the bot's first reading today").
- A real IV rank (store IV history); `FETCH_IV` is off because the current IV score is nearly constant.

### 4.11 Live streaming - L
- Angel One WebSocket for ticks instead of polling (today's scan takes minutes; spikes between scans can be missed). Needs a rewrite of the data layer and a long-running session design.

### 4.12 Real orders - L, far future
- Only after weeks of convincing paper results. SEBI/NSE algo rules: registered static IP, limit orders only, order-rate limits. GitHub's changing runner IPs would not work - needs a fixed-IP server.

---

## 5. Maintenance calendar

| When | Task |
|---|---|
| Every December | Refresh `market_holidays.json` with next year's NSE holidays (NSE publishes around December). |
| Monthly | Check the F&O stock list still resolves (auto-universe handles most changes); check Actions minutes (public repo = unlimited). |
| If login fails | Re-check the six GitHub secrets; regenerate the Angel One API key / TOTP secret if needed. |
| When the journal grows | `journal-data` is plain text and small; archive old months if it gets large. |
| After any change | Run the simulated checks, merge, and watch the next session's start message. |

## 6. Known limitations (be honest when reading results)
- Daily RSI/EMA signals plus the live price - not intraday patterns.
- Polling every minute or two, not streaming.
- "Estimated success probability" is a heuristic stretch of the score, not a measured rate.
- Paper fills assume 1% slippage; real spreads (especially cheap stock options late in the day) can be wider.
- Open-interest change is measured from the bot's first reading of the day.
- A few weeks of paper trades is a small sample; treat each review as evidence, not proof.

## 7. How to ask for a change
"Build item 4.X from `FUTURE_PLAN.md`" - one item at a time, then run for several sessions before the next.
