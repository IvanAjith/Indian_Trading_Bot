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

Answers from Ajith (26 Nov 2026) - now the agreed draft rules:
1. **Profit = the previous call reached at least Target 1.** (Stop moved to cost after T1 does not turn it into a loss.)
   **Loss = it closed without reaching T1** (stoploss, or time exit before T1).
2. **No new call on that underlying while its previous trade is still open.** A new call is allowed only after it is closed.
3. **No limit** on how many extra alerts per underlying per day - as long as the score is above 80 (and the other rules pass,
   e.g. 14:45 cutoff and the Rs 35,000 budget).
4. Applies to **all indexes and stocks**.
5. After a profit the new call must be at a **different strike** (any number of steps away, justified by the analysis) from every profitable call that day on that underlying.
   After a loss the **same strike is allowed**.
6. Applies to **both CE and PE** (direction does not matter; the rule is per underlying).

Final answers (Ajith, 26 Nov 2026):
- A trade closed by the **15:10 time exit** without reaching T1 is judged by its actual result: **positive P&L = profit, negative = loss**.
  (Reached T1 = profit; stoploss = loss.)
- The new strike after a profit is checked against **ALL of that day's profitable calls** on the underlying, not just the last one.
- The new strike does NOT have to be exactly one step away: **any number of steps is allowed**, but the choice must be logical, well analysed
  (see D2 criteria) and the score must be good enough (above 80). No random strike.

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

Note on D2 (Ajith, 26 Nov): the strike must be accurate and justified - analysed from the option chain, never "any strike that fits the
budget". One step away is the maximum distance to consider first; the alert must explain why the strike was picked.

## D3. FOUND 26 Nov 14:05 - stale "today's volume" explains the quiet morning and the 13:59 burst (BUG, not a rule)
Evidence (code): the screener's volume factor uses `snapshot.day_volume` = today's volume from the DAILY candle history
(`angelone_provider._make_snapshot`, `screener._volume_score`). That history is cached for 4 hours (`_history_cache_max_age = 4*3600`);
only the price is patched live (`_with_live_today`), NOT the volume.
Session #9 started 09:49, first history download ~09:55-10:00 -> today's volume frozen at its ~10:00 value (small) for 4 hours ->
"volume unremarkable" all morning, scores suppressed -> cache expired ~13:55-14:00 -> volumes refreshed to the afternoon
cumulative figure -> volume ratios 1.6x-2.0x -> scores jumped -> burst of alerts/watch messages at 13:59-14:05
(APLAPOLLO 1.6x, VMM 2.0x). To be confirmed from the run log at 15:35 (history refresh time, scan times).
Consequences: 26 Nov morning scores were understated; alerts cluster late (also true on 25 Nov: 14:52, 15:09).
Proposed fix (needs Ajith's OK): use the LIVE traded volume from the quote row each scan (getMarketData FULL has `tradeVolume`) instead of the cached candle.
Related strategy question (separate, after the review): cumulative volume vs a FULL-day 20-day average is naturally low in the morning and high late
in the day; a time-of-day-adjusted ratio (volume so far vs the usual volume by this time) would treat all hours fairly.

## Ideas log (add below)
- (none yet)

## Today's checks pending
- 15:35 IST: analyse run #9 log + journal (signals >=70, blocked by budget / once-per-day / cutoff, silent 65-69.9 results, heads-ups, scan times) and report.
- Evening: set up the cron-job.org outside timer (reminder set for 16:00 IST).
