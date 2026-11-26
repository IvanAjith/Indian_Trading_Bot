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
ALERT_SCORE = 70.0               # a Telegram ENTRY ALERT is sent for every signal scoring at least this
SHADOW_TRACK_LOWER_BAND = True   # signals between MIN_SIGNAL_SCORE and ALERT_SCORE send NO alert, but are paper-traded
                                 # silently and written to the journal, so the weekly review can tell whether the
                                 # lower band really is worse (and the alert score can be set from data, not a guess)
NO_ALERTS_AFTER = "14:45"        # no entry alerts or heads-ups from this IST time on: a trade opened later has too little time
                                 # before the 15:10 square-off. Signals after it are tracked silently (journal only).
                                 # None = no cutoff.
MAX_ALERTS_PER_SCAN = 2          # at most this many new alerts per scan, best score first. Others are NOT lost:
                                 # if they still qualify they alert in the next scan, a minute or two later
MAX_ALERTS_PER_DAY = None        # optional hard limit on alerts per day (None = no limit)
MAX_COST_PER_LOT = 35000         # skip signals whose contract costs more than this for ONE lot
                                 # (premium x lot size). Matches a small account: Rs1 lakh = ~3 such trades.
                                 # None = no budget filter. Skipped signals are counted in the daily summary.
MIN_SIGNAL_SCORE = 60.0          # floor for any signal at all: 60-69.9 are tracked SILENTLY (journal only; heads-ups still need 65+).
                                 # Lowered from 65 on 26 Nov 2026 so the nightly rule lab can test rules that lift a 61-64 signal into the alert range.
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
# Second (and later) alerts on the same stock/index the same day - allowed only when ALL of these hold:
#   - the previous call on it is CLOSED (never while it is still open),
#   - the new signal's score is ABOVE REALERT_MIN_SCORE,
#   - if a previous call was a PROFIT (reached Target 1, or closed in the green at the square-off), the new call
#     must be at a DIFFERENT strike from every profitable call that day; after a LOSS the same strike is fine.
# Applies to indexes and stocks, CE and PE. No daily limit (REALERT_MAX_PER_DAY = None).
REALERT_MIN_SCORE = 80.0
REALERT_MAX_PER_DAY = None         # None = no limit on extra alerts per underlying per day
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
ACCOUNT_SIZE = 100000.0           # your real account, used only to show "profit as % of the account" in summaries
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
QUICK_CHECK_SECONDS = 20   # between full scans, re-price OPEN trades this often (targets/stoploss alerts within ~20s)
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


# ---------------------------------------------------------------------------
# Volume factor (added 26 Nov 2026)
# ---------------------------------------------------------------------------
# The screener compares today's traded volume with the 20-day average. The bot now uses the LIVE traded volume from the
# quote on every scan (before, it used a daily candle cached for 4 hours, which made morning scores too low and caused a
# burst of alerts when the cache refreshed).
# Optional (OFF by default): compare with the volume that is usual BY THIS TIME OF DAY instead of a full day's average,
# so morning and afternoon signals are treated fairly. Turn on only after reviewing the journal.
VOLUME_TIME_ADJUST = False
# Share of a normal day's volume traded by each IST time (approximate NSE profile, used when VOLUME_TIME_ADJUST is on).
VOLUME_DAY_PROFILE = [("09:15", 0.00), ("09:30", 0.12), ("10:15", 0.25), ("11:15", 0.38), ("12:15", 0.50),
                      ("13:15", 0.60), ("14:15", 0.72), ("15:00", 0.88), ("15:30", 1.00)]
VOLUME_PROFILE_MIN_SHARE = 0.10    # never divide by less than this share (avoids huge ratios in the first minutes)

# ---------------------------------------------------------------------------
# Reasoned strike choice (added 26 Nov 2026)
# ---------------------------------------------------------------------------
# When the at-the-money contract costs more than MAX_COST_PER_LOT, or a repeat call must use a different strike, the bot
# looks along the option chain (ATM +-3 strikes) and picks the strike by reasons - never "any strike that fits the budget":
#   1. cost per lot <= MAX_COST_PER_LOT;  2. delta (estimated from neighbouring strikes) >= STRIKE_MIN_DELTA;
#   3. the underlying move needed for Target 1 <= STRIKE_MAX_MOVE_ATR_FRAC x the average daily range (ATR);
#   4. enough traded volume and open interest (liquidity);  5. not beyond a big opposing open-interest wall;
#   6. of what passes, the one CLOSEST to the money (highest delta). The alert states the reasons.
# CHEAPER_STRIKE_SHADOW: every over-budget signal is paper-traded SILENTLY at the reasoned strike, so the journal shows whether it works.
# CHEAPER_STRIKE_ALERTS: send real alerts for the reasoned strike (OFF until the journal supports it).
CHEAPER_STRIKE_SHADOW = True
CHEAPER_STRIKE_ALERTS = False
STRIKE_MIN_DELTA = 0.25
STRIKE_MAX_MOVE_ATR_FRAC = 0.35
STRIKE_MIN_VOLUME = 200
STRIKE_MIN_OI = 1000
STRIKE_OI_WALL_RATIO = 1.5         # an opposing OI "wall" = a strike with at least this x the OI of the candidate

# ---------------------------------------------------------------------------
# Scan speed and timing (added 26 Nov 2026)
# ---------------------------------------------------------------------------
# Most scan time went on downloading option quotes for ~100 symbols every scan (about 90 s per scan, so alerts and heads-ups
# arrived in bursts, minutes after a score crossed its threshold). Two-stage scan: stage 1 prices every symbol (spot + daily
# history, no option chain) and scores it without the option-OI part; stage 2 downloads option chains only for symbols that could
# still reach WATCHLIST_MIN_SCORE once the OI part is added (the OI part can add at most 9 points - see screener.needs_option_chain),
# plus the indexes and anything you hold. The scores of those symbols are identical to a full scan; the rest could not alert
# anyway. Every TWO_STAGE_FULL_EVERY-th scan (and the first of the day) is a full scan.
TWO_STAGE_SCAN = True
TWO_STAGE_FULL_EVERY = 20
# Before 09:20 the bot used to sit idle, then spend ~10 minutes downloading daily history for every symbol during the first scan.
# Now it downloads that history while it waits (from the moment it starts), so the first scan is quick.
PRELOAD_BEFORE_OPEN = True
# Silent tracking (paper trades below 70 or after the cutoff, journal only) stops at this time: a trade opened with a few minutes
# before the 15:10 square-off teaches nothing.
SILENT_TRACK_UNTIL = "14:55"

# ---------------------------------------------------------------------------
# Score changes learned from the 26 Nov 2026 journal (silent winners TCS and HCLTECH vs the rest) - added 26 Nov evening
# ---------------------------------------------------------------------------
# 1. Open interest must AGREE with the trade. A bullish setup earns the OI points only from call build-up / short covering,
#    a bearish setup only from put build-up / long unwinding; build-up in the opposite direction now scores 45 (neutral-low)
#    instead of 80. (26 Nov: stock trades with OI against them won 1 of 7, -Rs 12,452.)
OI_DIRECTION_AWARE = True
# 2. A stretched trend scores lower: a bullish stock with RSI >= STRETCHED_RSI_HIGH (or a bearish one with RSI <= STRETCHED_RSI_LOW)
#    loses STRETCHED_RSI_PENALTY points. Stocks only: indexes trend differently and the sample is one trade.
#    (26 Nov: stretched stock trades won 3 of 12, -Rs 28,190; not stretched won 5 of 9, +Rs 13,606.)
STRETCHED_RSI_PENALTY = 5.0
STRETCHED_RSI_HIGH = 70
STRETCHED_RSI_LOW = 30
# 3. Continuation bonus: a move of at least CONTINUATION_MIN_MOVE_PCT today IN the trade's direction, with agreeing open-interest
#    build-up and a trend that is not stretched, adds CONTINUATION_BONUS points. (TCS +2.5% and HCLTECH +4.6% both reached Target 1
#    and ran to +21-27%.) Set the penalty/bonus to 0 to switch either off. These come from ONE day of data (21 stock trades): the
#    weekly review checks them from now on, and they must be re-checked at the 11 Dec and 18 Dec checkpoints.
CONTINUATION_BONUS = 6.0
CONTINUATION_MIN_MOVE_PCT = 2.0

# ---------------------------------------------------------------------------
# Learning loop (added 26 Nov 2026) - learn every night, change the live rules only at the weekly meeting
# ---------------------------------------------------------------------------
# RULES_VERSION is stamped on every journal row, so the nightly rule lab knows which rules produced each score.
# Change it ONLY when the live scoring rules change (after a weekly-meeting decision).
RULES_VERSION = "v2-2026-11-26"
# Cap on silent paper trades per day (the lower 60-64.9 band adds trades; this keeps quote traffic bounded).
SILENT_MAX_PER_DAY = 40
# After each session the bot writes a nightly learning report (journal/lab/) and sends a short note to Telegram.
NIGHTLY_LEARNING_TELEGRAM = True
