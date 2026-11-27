# 27 Nov 2026 - live observations, update 2 (from the chat, session still running)

Telegram /trades snapshot pasted by Ajith (time not stamped; afternoon):
- 5 paper trades, 0 winners, 0 reached T1. Closed: ADANIPORTS 1920 CE -11,134, BEL 415 CE -7,666.50 (both stopped). Open: APLAPOLLO 1520 PE (entry 60.60, now 57.20, -2,380),
  ASHOKLEY 150 PE (entry 5.94, now 5.32, -6,200), HINDUNILVR 2140 CE (entry 51.56, now 50.05, -906). Net -28,286.50 = -12.6% of the money invested (plan A and plan B both -12.6%).
- Peak capital in use (plan A) 132,756 against the 1 lakh account.

## Observations to test at the meeting
1. **No index call today** so far; all five alerts are stock options. Check whether any index signal was blocked (budget, repeat rules) or never reached 70.
2. **ASHOKLEY PE premium 5.94**: lot about 5,000 (the -6,200 on a 0.62 fall over 2 lots implies it). A premium this small has a big relative spread and the -18% stop is worth about 1.07 rupees
   (a few ticks). 26 Nov: VMM CE at 4.35 also lost (-3,104). Lab candidate `min_premium_10`. Check premium vs result across the journal.
3. **HINDUNILVR 2140 CE again** (alerted 26 Nov 14:03 and lost -2,610; open -906 now). Repeat alerts on the same name on consecutive days: check whether names that lost yesterday lose again (no rule, just a look).
4. **Every trade today is below entry**: the market may be choppy/down; compare with NIFTY's move and breadth (spot_chg_pct of the alerted names, nifty_chg_pct).
5. Still to verify: volume units (ADANIPORTS 2.0x at 10:09), score_parts, scan times, silent 60-69.9 trades, and the candidates in journal/lab/candidates.md.

Nothing is changed in the live rules (RULES_VERSION v2-2026-11-26). Zero winners from five alerts is a small sample: judge it from the journal together with the silent trades.
