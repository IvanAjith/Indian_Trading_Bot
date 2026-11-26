# 26 Nov 2026 - why TCS and HCLTECH (silent, score 65-68) reached Target 1, and what to learn

Written after the session, from the 22 trades in `trades.jsonl` (6 alerted, 16 silent). One day, 21 stock trades: **these are hypotheses
for the weekly review to test, not rules.** The weekly review now tracks them automatically (new factors below).

## The three silent trades that reached Target 1
| Trade | Entry | Score | Day move | RSI | OI | Peak | Result |
|---|---|---|---|---|---|---|---|
| TCS 2580 CE | 13:59 | 68.4 | +2.5% | 66.9 | long build-up (agrees with a call) | +26.6% | +8,474 |
| HCLTECH 1440 CE | 14:26 | 65.0 | +4.6% | 64.0 | long build-up (agrees) | +20.9% | +7,492 |
| APLAPOLLO 1480 PE | 10:00 | 66.2 | +0.7% | 15.8 | flat | +25.8% in 2 min | +928 (stopped at cost; a spike, then reversed) |

## What TCS and HCLTECH had that the others did not
1. **A strong move in the trade's direction already under way** (+2.5% and +4.6% on the day) - continuation, not a bounce.
2. **Open interest agreeing with the trade** (call with CALL build-up).
3. **Trend not stretched** (RSI 64-67, below 70).
4. Time to run (entered with 45-70 minutes left; HCLTECH peaked at 14:58).

## How the 21 stock trades split (journal data)
| Group | Trades | Wins | Reached T1 | P&L |
|---|---|---|---|---|
| OI agreeing with the trade (call+long build-up, put+short build-up) | 11 | 6 | 2 | +6,046 |
| OI AGAINST the trade (call with short build-up, put with long build-up) | 7 | 1 | 0 | -12,452 |
| OI flat | 3 | 1 | 1 | -8,178 |
| RSI stretched (call RSI 70+ / put RSI 30-) | 12 | 3 | 1 | -28,190 |
| RSI not stretched | 9 | 5 | 2 | +13,606 |
| Day move in the trade's direction 2%+ | 5 | 3 | 2 | +8,818 |
| Day move under 2% | 16 | 5 | 1 | -23,402 |
| **Profile: move 2%+ AND OI agreeing AND not stretched** (TCS, HCLTECH, INFY, WIPRO) | 4 | 3 | 2 | +16,254 |
| Everything else | 17 | 5 | 1 | -30,838 |
INFY (15:00) and WIPRO (15:05) fit the profile but had only 5-10 minutes before the square-off.

## Findings to carry into the weekly meeting
- **The score does not separate these winners**: TCS/HCLTECH scored 65-68 (silent) while most 70+ alerts lost. The score has no notion of
  "stretched" RSI or of a strong same-direction day move.
- **The OI part of the score rewards OI build-up in the WRONG direction.** `screener._oi_signal` gives 80 points for long build-up and for
  short build-up whatever the trade's direction (+24 score points). The 7 "OI against" trades won 1 of 7. (The journal also called these "OI confirmed" - fixed.)
- **The journal's "chased" warning (avoid stocks that already moved 2%+) pointed the wrong way** for TCS and HCLTECH: the five 2%+ trades won 3 of 5.
  Do not turn "chased" into a rule; the review compares it with the new continuation profile.
- **RSI 70+ calls did badly** (3 wins of 12, -28,190): the move may already be exhausted.
- **Volume carried no signal today** (stale for most of the day; all trades flagged "weak"). Judge volume only from 27 Nov (live volume).

## New journal factors (from 27 Nov, and applied to old rows by the review)
`oi_confirmed` now requires OI to agree with the direction; new warnings `oi_against` and `stretched`; new good factor `continuation`.
The weekly review shows win rates with/without each (including good factors), and lists every silent trade that reached T1 with what it
shared, so the same pattern is not missed again.

## Decisions for the checkpoints (nothing changed yet)
D4a. Make the OI score direction-aware (a call needs CALL build-up, a put needs PUT build-up; the opposite earns nothing).
D4b. A penalty or filter for stretched RSI (calls 70+, puts 30-).
D4c. A bonus for the continuation profile. Test D4a-D4c on the silent journal first, then switch on one at a time.
