# DRAFT - ideas collected on 26 Nov 2026 (NOT built, NOT active)

Rule: nothing here is changed in the bot until Ajith says so after the end-of-day analysis. The running settings stay
frozen. Add new ideas to the bottom as they come.

## D1. Second alert on the same index/stock - new rule (replaces "score 90+ may alert once more")
Today: one alert per underlying per day; a second one only if score >= 90, once (`REALERT_MIN_SCORE`=90, `REALERT_MAX_PER_DAY`=1).

Requested:
- Threshold lowered from 90 to **80** (new signal score must be above 80).
- **If the previous call on that underlying closed in PROFIT:** the new call is allowed only at a **different strike** (different spot/strike price than the previous call).
- **If the previous call closed at a LOSS:** the new call is allowed at the **same strike** too (a fresh setup after a stop-out).
- Applies to the same underlying ("same indexes"); stocks included unless Ajith says indexes only.

Open questions to settle before building:
1. What counts as "profit" - the trade closed with a net gain (rupees, after slippage), or T1 was hit? (Suggested: net gain on the closed position.)
2. If the previous call is still OPEN when the new setup forms: block until it closes (suggested), or allow at a different strike?
3. Max number of extra alerts per underlying per day (currently 1)? Suggested: keep 1-2.
4. Indexes only (NIFTY, BANKNIFTY, FINNIFTY) or stocks too?
5. "Different strike" - any other strike, or at least one step (e.g. 50 points for NIFTY) away from the previous one?
6. Same direction only (CE after CE) or also the opposite (PE after CE)? Today direction is not part of the rule.

Where it lives: `main.py` `_alert_block_reason` and the re-alert state (`alerts_by_symbol`); needs the closed-trade result per underlying
from `paper_trader` (positions closed today) and the strike of the last call. Keep silent tracking of blocked signals.

## D2. Smarter strike choice when the at-the-money contract is over the Rs 35,000 per-lot budget
Today: a signal whose ATM contract costs more than Rs 35,000 per lot is skipped (alert not sent; tracked silently).

Requested workflow:
1. After the session, analyse the journal: how many signals were filtered (by budget, one-per-day, cutoff, etc.), and how many silent signals reached targets.
2. If it shows value, add logic that looks at the option chain and picks a **different strike whose one-lot cost is below Rs 35,000** and that still gives workable targets and stoploss.
3. The strike must be chosen **by reasoning, not just "the first one that fits the budget"**. It must be explainable and accurate enough to enter the trade.

Candidate selection criteria (to be validated on the journal data before use):
- Cost per lot <= Rs 35,000 (hard limit).
- Distance from spot: no more than 1-2 strikes out of the money, and the move needed to reach T1 (+15%) must be realistic versus the day's typical range (ATR / recent moves).
- Liquidity: traded volume and open interest above a minimum, a tight bid-ask spread (cheap far options are often illiquid).
- Open-interest context: avoid strikes sitting just beyond a large opposing OI wall; prefer strikes with supporting OI build-up.
- Delta / probability: a minimum delta so the option actually moves with the index (avoid "lottery tickets").
- Targets and stoploss recomputed from the new entry price with the same % rules (T1/T2/T3, SL 18%), and the alert must say WHY this strike was picked.
- Shadow-test first: for each over-budget signal, silently paper-track the candidate cheaper strikes (+1, +2) and compare with ATM (this is backlog item 4.2 in FUTURE_PLAN.md). Switch on only if the journal shows they earn.

The chain fetched now is ATM +-3 strikes, so offsets up to 3 need no new data calls.

## Ideas log (add below)
- (none yet)

## Today's checks pending
- 15:35 IST: analyse run #9 log + journal (signals >=70, blocked by budget / once-per-day / cutoff, silent 65-69.9 results, heads-ups, scan times) and report.
- Evening: set up the cron-job.org outside timer (reminder set for 16:00 IST).
