"""
config.py
---------
All tunable settings for the bot live here. Change numbers, not logic, when
you want to adjust risk or behaviour.
"""

# ---------------------------------------------------------------------------
# UNIVERSE
# ---------------------------------------------------------------------------
# Starter list of liquid NSE F&O underlyings. NSE revises the official F&O
# list quarterly (stocks get added/removed based on turnover & MWPL rules).
# Before going live, refresh this from the official list:
# https://archives.nseindia.com/content/fo/fo_mktlots.csv
INDEX_UNIVERSE = ["NIFTY", "BANKNIFTY", "FINNIFTY"]

# With the Angel One feed, the stock list below is only a "must keep" list.
# If AUTO_UNIVERSE is on, the bot also reads the live F&O contract list at the
# start of each day, ranks every F&O stock by how much money is parked in its
# near-the-money options (open interest value) and scans the top
# AUTO_UNIVERSE_MAX_STOCKS, plus everything in STOCK_UNIVERSE that still has
# live options. Wider coverage = a longer scan cycle (each ~40 option contracts
# cost one API call), so keep the number moderate.
AUTO_UNIVERSE = True
AUTO_UNIVERSE_MAX_STOCKS = 80

# Implied volatility needs one extra API call per symbol (every 5 min). It is
# currently a 10% score input that comes out the same for almost every symbol
# (it is compared against the same option chain it came from), so it is OFF by
# default - scores are unchanged, scans are much faster. Turn on to experiment.
FETCH_IV = False

STOCK_UNIVERSE = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "AXISBANK",
    "KOTAKBANK", "LT", "ITC", "BHARTIARTL", "MARUTI",
    # "TATAMOTORS" removed 2026-07 - Tata Motors demerged (Oct/Nov 2025) into
    # TMCV (commercial vehicles) and Tata Motors Passenger Vehicles Ltd;
    # confirm the correct current F&O symbol before re-adding.
    "TATASTEEL", "HINDUNILVR", "BAJFINANCE", "SUNPHARMA", "TITAN",
    "ADANIENT", "ADANIPORTS", "ULTRACEMCO", "ASIANPAINT", "WIPRO",
    "ONGC", "NTPC", "POWERGRID", "COALINDIA", "JSWSTEEL", "HCLTECH",
    "BAJAJFINSV", "M&M", "TECHM", "HINDALCO", "DRREDDY", "CIPLA",
    "EICHERMOT", "GRASIM", "BPCL", "SBILIFE", "HDFCLIFE", "DIVISLAB",
]

# ---------------------------------------------------------------------------
# TRADING STYLE (intraday, weekly-expiry options)
# ---------------------------------------------------------------------------
TRADE_STYLE = "INTRADAY"          # this bot is scoped to same-day setups
PREFERRED_EXPIRY = "NEAREST_WEEKLY"

# Strike selection relative to spot: 0 = ATM, 1 = one strike OTM, -1 = one ITM
STRIKE_OFFSET = 0

# ---------------------------------------------------------------------------
# SCREENER THRESHOLDS
# ---------------------------------------------------------------------------
MIN_VOLUME_SPIKE_RATIO = 1.5     # today's volume vs 20-day average
RSI_BULLISH_MIN = 55
RSI_BEARISH_MAX = 45
MIN_OI_CHANGE_PCT = 8.0          # % OI change to count as "buildup"
IV_PERCENTILE_LOOKBACK_DAYS = 252

TOP_N_SIGNALS_PER_DAY = 5        # (legacy default used by rank_universe; the real limits are the settings below)
ALERT_SCORE = 75.0               # a Telegram ENTRY ALERT is sent for every signal scoring at least this
SHADOW_TRACK_LOWER_BAND = True   # signals between MIN_SIGNAL_SCORE and ALERT_SCORE send NO alert, but are paper-traded
                                 # silently and written to the journal, so the weekly review can tell whether the
                                 # lower band really is worse (and the alert score can be set from data, not a guess)
MAX_ALERTS_PER_SCAN = 2          # at most this many new alerts per scan, best score first. Others are NOT lost:
                                 # if they still qualify they alert in the next scan, a minute or two later
MAX_ALERTS_PER_DAY = None        # optional hard limit on alerts per day (None = no limit)
MAX_COST_PER_LOT = 35000         # skip signals whose contract costs more than this for ONE lot
                                 # (premium x lot size). Matches a small account: Rs1 lakh = ~3 such trades.
                                 # None = no budget filter. Skipped signals are counted in the daily summary.
MIN_SIGNAL_SCORE = 70.0          # floor for any signal at all (70-74.9 are tracked silently, see ALERT_SCORE below)
                                  # (old comment follows) quality floor - below this, don't surface it
                                  # at all rather than filling out the count.
                                  # Some days will genuinely have 0-2 signals.
                                  # Raised from 65 -> 70 on 2026-07-08 for fewer,
                                  # more selective signals. NOTE: this is still not
                                  # a win-rate guarantee - a higher score means more
                                  # technical conditions lined up, not a higher
                                  # probability of avoiding the stoploss. Raise
                                  # further if you want fewer (noisier->cleaner)
                                  # ideas; lower it if you want more of them.

WATCHLIST_MIN_SCORE = 65.0       # symbols scoring in [WATCHLIST_MIN_SCORE, ALERT_SCORE)
                                  # get a lighter-weight "building setup, consider
                                  # watching this" alert instead of a full buy signal.
                                  # Raised from 50 -> 60 on 2026-07-08 (buy threshold is
                                  # 70) - a deliberate middle ground: tight enough to cut
                                  # most of the earliest, least-reliable false starts,
                                  # loose enough to still usually give a few real minutes
                                  # of lead time before a signal fires (a band this close
                                  # to the threshold, e.g. 65-68, mostly collapses the
                                  # watchlist and buy alerts into near-simultaneous
                                  # messages, which defeats the point of a heads-up).
                                  # Since scans repeat every MONITOR_INTERVAL_SECONDS,
                                  # something that shows up here now and crosses
                                  # MIN_SIGNAL_SCORE next cycle gives you a natural
                                  # few-minutes' heads up - not a guaranteed/precisely
                                  # timed warning, just how the scan cadence plays out.

WATCHLIST_MAX_PER_SCAN = 3       # cap how many watchlist alerts fire in a single scan
                                  # cycle, highest score first - since the bar for this
                                  # tier is deliberately low, many symbols can qualify
                                  # in the same cycle, and without a cap you'd get a
                                  # burst of separate Telegram messages all at once.
                                  # Candidates bumped by the cap aren't blocked forever -
                                  # they're simply reconsidered on the next scan.

# ---------------------------------------------------------------------------
# RISK / TARGETS / STOPLOSS (on the OPTION PREMIUM, not the underlying)
# ---------------------------------------------------------------------------
# Three staggered profit levels instead of one -- book partial quantity at
# each so you lock in gains progressively rather than all-or-nothing.
TARGET_PCT_T1 = 15.0               # book 40% of the position here
TARGET_PCT_T2 = 30.0               # book another 30% here
TARGET_PCT_T3 = 50.0               # let the last 30% run to here (or SL/time-exit)
# How a trade is built depends on what it is on:
#   INDEXES (NIFTY, BANKNIFTY, FINNIFTY): 3 lots, 3 targets - sell 1 lot at T1, 1 at T2, 1 at T3.
#   STOCKS:                               2 lots, 2 targets - sell 1 lot at T1, 1 at T2, then the trade is done.
# After T1 the stoploss on the rest moves to cost; for index trades, after T2 it trails up to the T1 price.
# (Set a LOTS_* value to None to size by the risk rule below instead.)
LOTS_PER_TRADE_INDEX = 3
TARGETS_INDEX = 3
LOTS_PER_TRADE_STOCK = 2
TARGETS_STOCK = 2
TRAIL_SL_TO_T1_AFTER_T2 = True     # once Target 2 is hit, move the stoploss up to the Target 1 price

TARGET_PCT = TARGET_PCT_T2         # kept for backward compatibility / backtest.py
STOPLOSS_PCT = 18.0                # cut the whole remaining position at -18%
MAX_CAPITAL_PER_TRADE_PCT = 5.0    # % of paper/live capital risked per trade
MAX_TRADES_PER_DAY = 3             # max open paper positions at once (ignored while
                                   # PAPER_TRACK_ALL_SIGNALS is True)
ONE_ALERT_PER_UNDERLYING_PER_DAY = True   # once a stock/index has produced an entry alert today, send NO
                                   # further alert or watch message on it for the rest of the day - whether
                                   # that trade hit its targets, hit the stoploss, or is still open; CE or PE,
                                   # any strike. (Trades you add with /add count as that stock's alert.)
REALERT_MIN_SCORE = 90.0           # ...unless a new signal on it scores at least this much (exceptional)
REALERT_MAX_PER_DAY = 1            # at most this many such exceptional extra alerts per stock/index per day
PAPER_TRACK_ALL_SIGNALS = True     # paper-trade EVERY alert the bot sends, so paper_trades.csv
                                   # and /trades match your alerts one-to-one. No cap on open
                                   # positions, and if one lot is bigger than the 5%-risk rule
                                   # allows it is still tracked as 1 lot (marked "oversize").
                                   # Set False for a strict, capital-limited simulation.
FORCE_EXIT_TIME = "15:10"          # square off every open (paper) trade at this IST time - nothing is carried overnight.
                                   # The session itself ends at 15:12 (SESSION_END): GitHub caps a job at 6 hours.

# How long before FORCE_EXIT_TIME to send an end-of-day check-in for any
# still-open position: current P&L if closed now, plus a reminder that
# auto square-off happens at FORCE_EXIT_TIME unless you reply
# "hold SYMBOL" on Telegram. Holding resets back to the default (auto
# square-off) every new trading day - it's a same-day-only override, not a
# standing instruction to carry positions indefinitely.
EOD_WARNING_MINUTES_BEFORE_CLOSE = 15

# ---------------------------------------------------------------------------
# PAPER TRADING
# ---------------------------------------------------------------------------
PAPER_STARTING_CAPITAL = 1000000.0   # 3 lots of an index option can cost ~Rs1.4 lakh each, so the paper account is Rs10 lakh
PAPER_LOG_FILE = "paper_trades.csv"
JOURNAL_DIR = "journal"                   # per-trade journal: daily markdown + trades.jsonl (see journal.py)
PAPER_RESULTS_FILE = "trade_results.csv"   # one row per CLOSED trade: Plan A (3-lot scale-out) vs Plan B (1 lot sold at T1)
MOVE_SL_TO_COST_AFTER_T1 = True   # once Target 1 is reached, move the stoploss up to the
                                  # entry price ("cost-to-cost") so the rest of the trade
                                  # cannot turn into a big loss. Trade-off: a normal dip
                                  # back to entry now stops you out at ~breakeven instead
                                  # of letting the trade breathe toward T2/T3.
PAPER_SLIPPAGE_PCT = 1.0     # assumed slippage per side (buy above / sell below the
                             # quoted price). Option spreads on NSE can be wider than
                             # this for illiquid strikes, so treat paper results with
                             # this setting as still slightly optimistic.

# ---------------------------------------------------------------------------
# SCHEDULE NOTES
# ---------------------------------------------------------------------------
# NSE cash/F&O market hours: 09:15 - 15:30 IST. Pre-open session 09:00-09:08.
# There is NO live option-chain data at 08:00 -- the exchange is closed.
# Two distinct runs are recommended:
#   08:00  -> PRE-MARKET PREP: previous close technicals + OI buildup +
#             global cues (GIFT Nifty, Dow futures, crude, DXY) -> watchlist
#   09:20  -> LIVE SIGNAL RUN: once live LTP/OI is flowing, confirm bias on
#             the watchlist and emit actual entry/target/SL
PRE_MARKET_RUN_TIME = "08:00"
LIVE_SIGNAL_RUN_TIME = "09:20"

# --mode monitor runs continuously between these two times, then exits for
# the day. It re-scans every MONITOR_INTERVAL_SECONDS and only alerts on
# setups it hasn't already alerted on today (see main.py run_monitor).
# Note: NSE pre-open is 09:00-09:08, continuous matching starts 09:15 - 09:00
# is fine as a "start watching" time, quotes just won't be fully live until 09:15.
MARKET_OPEN_TIME = "09:00"
MARKET_CLOSE_TIME = "15:30"
import os as _os
MONITOR_INTERVAL_SECONDS = int(_os.environ.get("TB_SCAN_INTERVAL", 180))   # env var overrides (GitHub Actions uses 30) # 3 min between scans - tune against your
                                 # broker's rate limits if you widen the universe
STATUS_POLL_INTERVAL_SECONDS = 15   # how often to check Telegram for a "status" ping

# ---------------------------------------------------------------------------
# TELEGRAM ALERTS
# ---------------------------------------------------------------------------
# As of 2026-07-08, credentials live ONLY in environment variables - NOT in
# this file - since this project now lives in a git repo (even a private
# one shouldn't hold a live bot token in its history). Set these on every
# device you run the bot from (on GitHub Actions: repo Secrets, see GITHUB_ACTIONS_DEPLOY.md):
#   TB_TELEGRAM_TOKEN    - your bot's token from @BotFather
#   TB_TELEGRAM_CHAT_ID  - your chat_id (see README "Telegram alerts")
# telegram_notifier._get_credentials() reads these first - config.py's
# values below are just a harmless empty fallback, never the real thing.
TELEGRAM_ENABLED = True
TELEGRAM_BOT_TOKEN = ""   # intentionally blank - set TB_TELEGRAM_TOKEN instead
TELEGRAM_CHAT_ID = ""     # intentionally blank - set TB_TELEGRAM_CHAT_ID instead
