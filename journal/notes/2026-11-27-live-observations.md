# 27 Nov 2026 - live observations from the chat (written at about 12:30 IST, before the session ended)

Facts come from Ajith's Telegram messages and TradingView screenshots pasted into the chat; the journal and run log are not yet available
mid-session, so everything below must be CHECKED against them after 15:12. One clean day so far, tiny samples: hypotheses only.

## Plumbing (works)
- cron-job.org timer started the bot at **09:00:22 IST** (run #12); the 09:10 backup (run #13) queued and exits after the session ends.
- First scan **2.2 min for 101 symbols** (26 Nov: 10.4 min). History pre-load before the open works.

## Alerts seen so far
| Time | Trade | Score | What the alert said | Outcome |
|---|---|---|---|---|
| 10:09 | ADANIPORTS 1920 CE @ 56.30 (lot 475, 2 lots) | 73 | daily EMA9>EMA20; volume 2.0x; OI long build-up | stopped: -5,567/lot, -11,134 |
| 10:38 | BEL 415 CE @ 13.25 (lot 1425, 2 lots) | 70 | daily EMA9>EMA20; broke 20-day resistance; OI long build-up; +2.4% move (continuation bonus) | stopped: -3,833/lot, -7,666 |
| 12:13 | APLAPOLLO 1520 PE @ 60.00 (lot 350) | 70 | daily EMA9<EMA20; volume 1.6x | open at time of writing |
Two stops so far = -18,800 = -20.4% of the money invested (the stop is -18% on the option price; 1% slippage each side adds about 2%).

## What the charts showed (Ajith's screenshots)
1. **Daily vs 15-minute confusion.** Ajith's 15-minute chart of the ADANIPORTS OPTION showed EMA9 below EMA20, while the alert said "EMA9 above EMA20
   (bullish trend)". The alert was right: it is the STOCK's DAILY EMA9 (1,911) above EMA20 (1,892). The wording was ambiguous -> now says "daily", and each
   alert shows the stock's 15-minute trend (information only; logged in the journal as `intraday`).
2. **Thin daily trend.** ADANIPORTS daily: EMA gap about 1% and narrowing, price (1,902) below EMA9 (1,911), stock -0.47% on the day, lower highs, mostly red
   candles. The bot's trend test is yes/no (EMA9 above EMA20) and ignores strength. -> daily EMA gap, price vs EMA9 and the day move are now logged
   (`trend_daily`); lab candidates `daily_price_vs_ema9`, `daily_ema_gap_1pct`.
3. **Alert against the day's move.** APLAPOLLO PE: the put premium was -30% on the day (95 -> 60), i.e. the stock was rallying strongly, yet a put alerted (score exactly 70).
   The score has no notion of the day's move against the trade, and the volume part is not direction-aware (high volume on a rally credits a put).
   26 Nov data: stock trades with the day's move against the trade (<0): 9 trades, 2 wins, -16,046; flat/with: 12 trades, 6 wins, +1,462 (one day, small).
   -> lab candidates `day_move_not_against`, `volume_credit_only_if_move_agrees`.
4. **Bare-70 alerts.** BEL (70, helped by the +6 continuation bonus) and APLAPOLLO (70, after the -5 stretched penalty) sit exactly on the threshold. The review should
   look at win rate by score band 70-72 vs 73+.

## To verify after the session (checklist for the weekly meeting)
- **Volume units:** ADANIPORTS showed "volume 2.0x the 20-day average" at 10:09 (about 50 minutes of trading). Check `volume_ratio` of all trades by time of day in the journal: if most
  stocks show 1x+ in the first hour, the live `tradeVolume` is in different units -> fix before Monday.
- BEL / ADANIPORTS / APLAPOLLO `score_parts` (mom, vol, oi, iv, penalty, bonus): how much came from the volume part and the continuation bonus?
- Scan health: average and slowest scan time (target ~30 s; 26 Nov: avg 91 s), rate-limit retries, two-stage scan working.
- Silent trades 60-69.9 (new floor 60): count, win rate, Target 1 rate; alerted vs silent.
- Any cheaper-strike alert or index repeat call; whether the 2-step limit skipped signals.
- Nightly learning note and Friday digest arrived on Telegram.
- Lab candidates: all 'too early' (one out-of-sample day) - say so plainly.

## Candidates now in the lab (live rules unchanged, RULES_VERSION v2-2026-11-26)
rsi_cap_65, stretched_penalty_10, continuation_bonus_10, oi_must_agree, move_1pct_with_trade, window_0950_1430, day_move_not_against,
volume_credit_only_if_move_agrees, daily_price_vs_ema9, daily_ema_gap_1pct, intraday_15m_must_agree.
