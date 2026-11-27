# 27 Nov 2026 - incident (session cut off at 15:00) and findings from the journal

## Incident
- The bot was started at **09:00:27 IST** by the outside timer (run 45934553403). GitHub stops a hosted job **6 hours after it starts**: the step "Run bot until session end" was
  CANCELLED at **15:00:39 IST** (job started 03:30:27Z, cancelled 09:30:39Z), before the 15:10 square-off and the 15:12 session end.
- Consequences: (1) trades still open were never squared off and no end-of-day summary was sent for the real session; (2) the journal holds only the trades that had CLOSED before 15:00
  (10 rows) - open ones are missing; (3) the queued backup run (run 45935516469, the 09:10 call) started when the first job ended and ran a second 'session' 15:01-15:12 with an empty state
  ("Nothing to report yet today", first scan 7.2 min, avg scan 44 s, 141 rate-limit retries of 414 calls).
- Open at 14:56 (Telegram check-in): APLAPOLLO 1520 PE entry 60.60 now 57.20 (-2,380); ASHOKLEY 150 PE entry 5.94 now 5.33 (-6,100); HINDUNILVR 2140 CE entry 51.56 now 49.95 (-966). Day total of the
  alerted trades at those prices about -28,250 (not a real square-off; true exits unknown). Silent trades still open at 15:00 are also missing.
- Fix: the bot now ends itself at start + 358 minutes (squaring off 2 minutes earlier, with a warning in the start message); timers moved to 09:15 and 09:25 (a 09:15 start gives 15:13).
- **27 Nov is an INCOMPLETE day**: the rule lab excludes it from out-of-sample evidence.

## The 10 closed trades (journal)
| Entry | Trade | Alert | Score | NIFTY% | Stock% | RSI | OI | Result | Exit |
|---|---|---|---|---|---|---|---|---|---|
| 09:22 | NIFTY 25850 CE | silent | 63.4 | -0.13 | -0.13 | 71 | flat | -6,708 | stop 10:23 |
| 09:29 | ADANIPORTS 1940 CE | silent | 63.4 | -0.27 | +1.02 | 61 | flat | -11,039 | stop 09:42 |
| 09:40 | IDFCFIRSTB 89 CE | silent | 67.8 | -0.07 | +2.38 | 69 | long build-up | -14,098 | stop 10:53 |
| 09:43 | DRREDDY 1400 CE | silent | 60.7 | -0.08 | +0.47 | 67 | long build-up | -10,312 | stop 10:58 |
| 09:43 | MAHABANK 97 CE | silent | 64.4 | -0.07 | +2.85 | 64 | long build-up | -12,220 | stop 11:31 |
| 10:07 | M&M 3500 CE | silent | 60.9 | -0.17 | +0.03 | 67 | long build-up | -8,540 | stop 13:25 |
| 10:08 | ADANIPORTS 1920 CE | **ALERT** | 72.9 | -0.08 | +0.04 | 57.5 | long build-up | -11,134 | stop 11:56 |
| 10:37 | BEL 415 CE | silent | 68.6 | -0.45 | +2.05 | 66 | long build-up | -7,780 | stop 11:20 |
| 10:38 | BEL 415 CE | **ALERT** | 70.0 | -0.41 | +2.41 | 66 | long build-up | -7,666 | stop 11:16 |
| 12:25 | ASHOKLEY 152.5 PE | silent | 62.4 | -0.69 | -2.64 | 40.6 | short build-up | **+3,550** | stop at cost after T1 13:46 (peak +17.3%) |

## Findings (one incomplete day; hypotheses)
1. **A one-way bet on a weak day.** 9 of the 10 closed trades were CALLS; NIFTY was flat to -0.7% all morning. All 9 calls were stopped out (best peak +11%). The only winner was the **PUT** that agreed with the day
   (ASHOKLEY: stock -2.64%, NIFTY -0.69%, OI short build-up). The bot has no market-direction filter. -> lab candidate `nifty_with_trade` (in-sample today it would have dropped both alerts).
2. **Volume units are fine.** Early-morning volume ratios were 0.07-0.67 for most names (normal: a few hours of a day's volume) and 1.87-1.99 only for ADANIPORTS (a genuine high-volume stock). No unit error.
3. **ADANIPORTS 72.9 was carried by volume**: momentum part only 55 (RSI just above 55), volume part 99 (ratio 2.0x). -> lab candidate `momentum_part_min_60`.
4. **BEL 70.0 was carried by the +6 continuation bonus** (64.0 without it) and lost. ASHOKLEY PE (the winner) also had the bonus but scored only 62.4, so it stayed silent - the +6 would not have been enough.
   One day: the continuation profile has now one loser (BEL) and one near-miss winner (ASHOKLEY) - the weekly review compares them.
5. **Silent trades dominated**: 8 of 10 rows are silent (floor 60). The ones at 60-65 with volume ratio < 0.3 had a volume part of 0 and were all stopped out.
6. BEL: a silent trade (68.6) and the alert (70.0) on the same contract a minute apart - both rows exist (the lab counts only the alert).
7. Intraday (15-minute) and daily-trend fields are absent today (the session ran the earlier code); they start with the next session.
