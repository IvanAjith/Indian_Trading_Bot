# Rule lab - candidates (cumulative)

## Candidate rules (the live rules do not change until the weekly meeting approves one)

Only out-of-sample days (after the candidate was proposed, clean data, live volume) count as evidence.

### rsi_cap_65 - momentum earns no extra credit for RSI above 65 (calls) / below 35 (puts) (proposed 2026-11-26)
- In-sample (shaped by these days, do not trust): live 7 alerts, 5 wins, 3 reached T1, +₹16,769; with the rule 3 alerts, 2 wins, 2 reached T1, +₹12,862.
- **Out-of-sample:** live 2 alerts, 0 wins, 0 reached T1, -₹18,800; with the rule 1 alerts, 0 wins, 0 reached T1, -₹11,134.
- Trade-off out-of-sample: drops 0 winner(s), avoids 1 loser(s), adds 0 winner(s) and 0 loser(s).
- Verdict: **too early** (2 out-of-sample alerts; need about 30).

### stretched_penalty_10 - stretched-trend penalty doubled (stocks): -10 instead of -5 (proposed 2026-11-26)
- In-sample (shaped by these days, do not trust): live 7 alerts, 5 wins, 3 reached T1, +₹16,769; with the rule 4 alerts, 3 wins, 3 reached T1, +₹20,201.
- **Out-of-sample:** live 2 alerts, 0 wins, 0 reached T1, -₹18,800; with the rule 2 alerts, 0 wins, 0 reached T1, -₹18,800.
- Trade-off out-of-sample: drops 0 winner(s), avoids 0 loser(s), adds 0 winner(s) and 0 loser(s).
- Verdict: **too early** (2 out-of-sample alerts; need about 30).

### continuation_bonus_10 - continuation profile bonus raised to +10 (from +6) (proposed 2026-11-26)
- In-sample (shaped by these days, do not trust): live 7 alerts, 5 wins, 3 reached T1, +₹16,769; with the rule 7 alerts, 5 wins, 3 reached T1, +₹16,769.
- **Out-of-sample:** live 2 alerts, 0 wins, 0 reached T1, -₹18,800; with the rule 3 alerts, 0 wins, 0 reached T1, -₹33,012.
- Trade-off out-of-sample: drops 0 winner(s), avoids 1 loser(s), adds 0 winner(s) and 2 loser(s).
- Verdict: **too early** (3 out-of-sample alerts; need about 30).

### oi_must_agree - no alert unless open interest agrees with the trade (stocks) (proposed 2026-11-26)
- In-sample (shaped by these days, do not trust): live 7 alerts, 5 wins, 3 reached T1, +₹16,769; with the rule 6 alerts, 5 wins, 3 reached T1, +₹21,291.
- **Out-of-sample:** live 2 alerts, 0 wins, 0 reached T1, -₹18,800; with the rule 2 alerts, 0 wins, 0 reached T1, -₹18,800.
- Trade-off out-of-sample: drops 0 winner(s), avoids 0 loser(s), adds 0 winner(s) and 0 loser(s).
- Verdict: **too early** (2 out-of-sample alerts; need about 30).

### move_1pct_with_trade - no stock alert unless the stock is already 1%+ in the trade's direction today (proposed 2026-11-26)
- In-sample (shaped by these days, do not trust): live 7 alerts, 5 wins, 3 reached T1, +₹16,769; with the rule 6 alerts, 5 wins, 3 reached T1, +₹21,291.
- **Out-of-sample:** live 2 alerts, 0 wins, 0 reached T1, -₹18,800; with the rule 1 alerts, 0 wins, 0 reached T1, -₹7,666.
- Trade-off out-of-sample: drops 0 winner(s), avoids 1 loser(s), adds 0 winner(s) and 0 loser(s).
- Verdict: **too early** (2 out-of-sample alerts; need about 30).

### window_0950_1430 - alerts only between 09:50 and 14:30 (skip the opening half hour and the last stretch) (proposed 2026-11-26)
- In-sample (shaped by these days, do not trust): live 7 alerts, 5 wins, 3 reached T1, +₹16,769; with the rule 7 alerts, 5 wins, 3 reached T1, +₹16,769.
- **Out-of-sample:** live 2 alerts, 0 wins, 0 reached T1, -₹18,800; with the rule 2 alerts, 0 wins, 0 reached T1, -₹18,800.
- Trade-off out-of-sample: drops 0 winner(s), avoids 0 loser(s), adds 0 winner(s) and 0 loser(s).
- Verdict: **too early** (2 out-of-sample alerts; need about 30).
