# 27 Nov 2026 - two calls from a SEBI-registered advisor (via Ajith) that the bot did not give

Reported by Ajith in the chat; times and prices as he gave them (not verified against market data).

| Call | Received | Entry | Targets | Stop | Outcome (as reported) |
|---|---|---|---|---|---|
| NIFTY 25850 PE | about 10:54 | 218 | 263 / 289 | not given | T1 hit 11:21, T2 hit 13:15 |
| VEDL 29 DEC 250 CE | 13:33 | 9 | 10.5 / 13 | 7.4 | doing fine; advisor holds it (positional) |

If the bot had traded the NIFTY PE under its own plan (3 lots, T1 +15% = 250.7, T2 +30% = 283.4, 1% slippage each side): about +5,700 for the first two lots (65 per lot), third lot open to a trailing stop.
For VEDL the advisor's stop (7.4) is -17.8% and T1 (10.5) +16.7%, i.e. about the bot's own -18% / +15%: the geometry matches, so the difference is WHEN and WHICH.

## Why the bot did not give them
1. **NIFTY PE - structural.** The bot's direction comes from the DAILY trend (RSI above 55 with EMA9 above EMA20 = bullish; below 45 with EMA9 below EMA20 = bearish). NIFTY's daily trend was bullish all day
   (daily RSI about 71 at 09:22; journal row: NIFTY CE 25850 silent at 09:22, score 63.4, stopped out 10:23, -6,708). A PUT on NIFTY therefore cannot be produced on a day when the daily trend is bullish, however weak the day
   is intraday. The advisor traded an intraday reversal; the bot only follows the daily trend. The same pattern showed in the stocks: 9 calls lost on a weak day, the one put (aligned with the day) won.
2. **VEDL - not verified.** VEDL is not in the curated STOCK_UNIVERSE; it is scanned only if it is among the top 80 F&O stocks by option open-interest value (the bot scanned 101 symbols), which is unknown. Even if scanned, a
   signal at 60-69.9 would be silent, and trades still open at the 15:00 cut-off are missing from the journal, so the journal cannot show whether VEDL was tracked. Check: the scanned universe list (not logged by name yet).

## Ideas this suggests (not built)
- Intraday market-regime signal for the INDEXES (NIFTY/BANKNIFTY/FINNIFTY): 15-minute EMA9 vs EMA20, price vs VWAP and the day's change decide direction (allow a put when the daily trend is up but the day is
  breaking down), logged every few minutes as a shadow series and as silent index trades, so the lab can test it.
- Log the scanned universe by name each day; add metals (VEDL, HINDALCO, JSWSTEEL, TATASTEEL, NMDC...) to the curated list; raise AUTO_UNIVERSE_MAX_STOCKS (two-stage scans make a wider universe cheap).
- Keep an external-calls ledger (this file) and replay each advisor call against what the bot saw at that time. Two winners are not a sample: Ajith to send ALL of the advisor's calls (winners and losers) with times.
