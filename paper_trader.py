"""
paper_trader.py
---------------
Virtual order book. No broker order-placement call happens anywhere in this
file - it only reads live LTP and simulates fills/exits so you can validate
the strategy with fake money before risking real capital.

Supports staggered partial exits across T1/T2/T3 (config.TARGET_ALLOCATIONS)
instead of one all-or-nothing target, matching how the signals are framed.

Every fill (partial or full) is appended to a CSV (config.PAPER_LOG_FILE) so
you can review win-rate/drawdown after a few weeks of paper trading.
"""

import csv
import os
import datetime as dt
from dataclasses import dataclass, field, asdict
from typing import Optional

from strategy import TradeIdea, position_size
import config


@dataclass
class Fill:
    symbol: str
    option_type: str
    strike: float
    expiry: str
    level: str          # "T1", "T2", "T3", "SL", "TIME", "MANUAL"
    lots: int
    price: float
    pnl: float
    at: str
    entry: float = 0.0          # premium at entry - lets you recompute % return per fill
    score: float = 0.0          # screener score AT ENTRY TIME (not re-evaluated later)
    win_probability: float = 0.0  # heuristic estimate at entry time (see strategy.py)
    lot_size: int = 1            # units per lot (pnl = price move x lots x lot_size)
    reasons: str = ""            # semicolon-joined technical triggers that fired at entry -
                                  # the point of these last four fields: enough context per
                                  # row to later analyze WHICH setups/score-bands/reasons
                                  # actually win, not just the overall P&L.


@dataclass
class PaperPosition:
    symbol: str
    option_type: str
    strike: float
    expiry: str
    entry: float
    target1: float
    target2: float
    target3: float
    stoploss: float
    lots_total: int
    opened_at: str
    lots_remaining: int = 0
    lots_by_level: dict = field(default_factory=dict)   # {"T1": n, "T2": n, "T3": n}
    booked_levels: set = field(default_factory=set)
    status: str = "OPEN"        # OPEN, PARTIAL, CLOSED
    fills: list = field(default_factory=list)
    held_over: bool = False     # True = user asked to skip today's auto square-off
                                 # (via the "hold SYMBOL" Telegram command). Resets
                                 # to False at the next day rollover - a same-day-only
                                 # override, not a standing "always carry" instruction.
    score: float = 0.0           # carried over from the TradeIdea that opened this,
                                 # so it can be logged alongside every fill/exit.
    win_probability: float = 0.0
    reasons: list = field(default_factory=list)
    lot_size: int = 1
    oversize: bool = False       # tracked as 1 lot although it breaks the max-loss rule
    t1_alerted: bool = False    # True once the "Target 1 reached" alert has been sent

    def __post_init__(self):
        if not self.lots_by_level and self.lots_total == 1:
            # One lot cannot be split into partial exits - take it all at T2.
            self.lots_by_level = {"T1": 0, "T2": 1, "T3": 0}
        if not self.lots_by_level:
            t1 = round(self.lots_total * config.TARGET_ALLOCATIONS[0])
            t2 = round(self.lots_total * config.TARGET_ALLOCATIONS[1])
            t3 = self.lots_total - t1 - t2  # remainder soaks up rounding
            self.lots_by_level = {"T1": t1, "T2": t2, "T3": t3}
        if self.lots_remaining == 0:
            self.lots_remaining = self.lots_total


class PaperTradingAccount:
    def __init__(self, starting_capital: float = None, log_file: str = None, on_fill=None, on_t1=None):
        """on_fill, if given, is called as on_fill(position, level, lots, price)
        every time a target/stoploss/time-exit fill happens - main.py wires
        this to send a Telegram message. Kept as an optional callback so
        this module stays notification-agnostic."""
        self.capital = starting_capital or config.PAPER_STARTING_CAPITAL
        self.log_file = log_file or config.PAPER_LOG_FILE
        self.open_positions: list[PaperPosition] = []
        self.on_fill = on_fill
        self.on_t1 = on_t1   # on_t1(position, price): called once when Target 1 is first reached
        self._ensure_log_header()

    def _ensure_log_header(self):
        expected_fields = list(asdict(Fill("", "", 0, "", "", 0, 0, 0, "")).keys())

        if os.path.exists(self.log_file) and os.path.getsize(self.log_file) > 0:
            with open(self.log_file) as f:
                existing_header = f.readline().strip().split(",")
            if existing_header == expected_fields:
                return  # already up to date - leave existing data alone
            # Columns changed (e.g. this update added entry/score/reasons) -
            # archive the old file rather than silently write mismatched
            # columns into it. Your old data isn't lost, just renamed.
            today_str = dt.date.today().isoformat()
            backup_name = f"{self.log_file}.pre_{today_str}.bak"
            suffix = 2
            while os.path.exists(backup_name):
                backup_name = f"{self.log_file}.pre_{today_str}_{suffix}.bak"
                suffix += 1
            os.rename(self.log_file, backup_name)
            print(f"[paper_trader] Trade log columns changed - archived your old log to "
                  f"'{backup_name}' (nothing lost) and started a fresh '{self.log_file}' "
                  f"with the new columns.")

        with open(self.log_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=expected_fields)
            writer.writeheader()

    def open_trade(self, idea: TradeIdea, lots: int = None) -> PaperPosition:
        lots = lots if lots is not None else position_size(self.capital, idea.entry, idea.lot_size)
        oversize = False
        if lots <= 0 and config.PAPER_TRACK_ALL_SIGNALS:
            lots, oversize = 1, True
            print(f"[paper] {idea.symbol} {idea.strike:.0f} {idea.option_type}: one lot is above the "
                  f"{config.MAX_CAPITAL_PER_TRADE_PCT}% risk rule - tracking it as 1 lot anyway (oversize).")
        if lots <= 0:
            print(f"[paper] skipped {idea.symbol} {idea.strike:.0f} {idea.option_type}: one lot "
                  f"({idea.lot_size} units x Rs{idea.entry:.2f}) is too big for the "
                  f"{config.MAX_CAPITAL_PER_TRADE_PCT}% risk limit on Rs{self.capital:.0f}.")
            return None
        # Realistic fill: you buy at the ask / chase the move, so assume the
        # entry fills slightly ABOVE the signal price (config.PAPER_SLIPPAGE_PCT).
        slip = config.PAPER_SLIPPAGE_PCT / 100
        pos = PaperPosition(
            symbol=idea.symbol,
            option_type=idea.option_type,
            strike=idea.strike,
            expiry=idea.expiry,
            entry=round(idea.entry * (1 + slip), 2),
            target1=idea.target1,
            target2=idea.target2,
            target3=idea.target3,
            stoploss=idea.stoploss,
            lots_total=lots,
            opened_at=dt.datetime.now().isoformat(timespec="seconds"),
            score=idea.score,
            win_probability=idea.win_probability,
            reasons=list(idea.reasons) if idea.reasons else [],
            lot_size=idea.lot_size,
            oversize=oversize,
        )
        self.open_positions.append(pos)
        return pos

    def open_manual(self, symbol: str, option_type: str, strike: float, entry: float, expiry: str,
                    lot_size: int, lots: int = None) -> PaperPosition:
        """Register a trade you took yourself. Uses YOUR entry price as-is (no extra slippage), the
        usual stoploss/targets from config, and tracks it like any other paper trade."""
        if lots is None:
            lots = max(position_size(self.capital, entry, lot_size), 1)
        pos = PaperPosition(
            symbol=symbol, option_type=option_type, strike=strike, expiry=expiry, entry=round(entry, 2),
            target1=round(entry * (1 + config.TARGET_PCT_T1 / 100), 2),
            target2=round(entry * (1 + config.TARGET_PCT_T2 / 100), 2),
            target3=round(entry * (1 + config.TARGET_PCT_T3 / 100), 2),
            stoploss=round(entry * (1 - config.STOPLOSS_PCT / 100), 2),
            lots_total=lots, opened_at=dt.datetime.now().isoformat(timespec="seconds"),
            reasons=["manual entry (added via /add)"], lot_size=lot_size,
        )
        self.open_positions.append(pos)
        return pos

    def mark_to_market(self, symbol: str, option_type: str, strike: float, current_ltp: float):
        """Call this repeatedly (e.g. every 1-5 min during market hours) with
        fresh LTP for each open leg to book partial targets / stoploss / the
        end-of-day time exit."""
        now = dt.datetime.now()
        force_exit = now.strftime("%H:%M") >= config.FORCE_EXIT_TIME

        for pos in self.open_positions:
            if pos.status == "CLOSED" or pos.lots_remaining <= 0:
                continue
            if not (pos.symbol == symbol and pos.option_type == option_type and pos.strike == strike):
                continue

            # Full stoploss on the remaining quantity takes priority.
            if current_ltp <= pos.stoploss:
                self._fill(pos, "SL", pos.lots_remaining, current_ltp)
                continue

            # Target 1 reached: alert once (even for single-lot trades, which have no
            # partial exit at T1), and optionally lock the stop at cost-to-cost.
            if not pos.t1_alerted and current_ltp >= pos.target1:
                pos.t1_alerted = True
                if config.MOVE_SL_TO_COST_AFTER_T1:
                    pos.stoploss = max(pos.stoploss, pos.entry)
                if self.on_t1:
                    try:
                        self.on_t1(pos, current_ltp)
                    except Exception as exc:
                        print(f"[paper_trader] on_t1 callback failed (not fatal): {exc}")

            for level, target_price in (("T1", pos.target1), ("T2", pos.target2), ("T3", pos.target3)):
                if level in pos.booked_levels:
                    continue
                if current_ltp >= target_price:
                    lots_here = min(pos.lots_by_level.get(level, 0), pos.lots_remaining)
                    if lots_here > 0:
                        self._fill(pos, level, lots_here, current_ltp)
                        pos.booked_levels.add(level)

            if force_exit and pos.lots_remaining > 0 and not pos.held_over:
                self._fill(pos, "TIME", pos.lots_remaining, current_ltp)

    def manual_close(self, pos: PaperPosition, price: float):
        """Immediately closes a position at a user-supplied/observed price -
        used when you've exited manually outside the bot (e.g. via your
        broker's app, especially for a held-over position) and tell the bot
        via the 'close SYMBOL [price]' Telegram command, so the day's
        trade-room count and P&L summary stay accurate."""
        if pos.status == "CLOSED" or pos.lots_remaining <= 0:
            return
        self._fill(pos, "MANUAL", pos.lots_remaining, price)

    def close_all(self, ltp_lookup, level: str = "TIME"):
        """Closes every still-open position at the latest known price.
        ltp_lookup(position) -> price or None. Used when a run/session ends
        so no paper position is left dangling. Returns the number closed."""
        closed = 0
        for pos in self.open_positions:
            if pos.status == "CLOSED" or pos.lots_remaining <= 0:
                continue
            price = ltp_lookup(pos)
            if price is None:
                continue
            self._fill(pos, level, pos.lots_remaining, price)
            closed += 1
        return closed

    def _fill(self, pos: PaperPosition, level: str, lots: int, price: float):
        # Selling: you hit the bid, so assume the exit fills slightly BELOW the
        # quoted price (same slippage % as entry). Targets/stoploss triggers
        # were already decided on the quoted price before this point.
        price = round(price * (1 - config.PAPER_SLIPPAGE_PCT / 100), 2)
        pnl = round((price - pos.entry) * lots * pos.lot_size, 2)
        fill = Fill(
            symbol=pos.symbol, option_type=pos.option_type, strike=pos.strike,
            expiry=pos.expiry, level=level, lots=lots, price=price, pnl=pnl,
            at=dt.datetime.now().isoformat(timespec="seconds"),
            entry=pos.entry, score=pos.score, win_probability=pos.win_probability, lot_size=pos.lot_size,
            reasons="; ".join(pos.reasons) if pos.reasons else "",
        )
        pos.fills.append(fill)
        pos.lots_remaining -= lots
        self.capital += pnl
        self._append_log(fill)
        pos.status = "CLOSED" if pos.lots_remaining <= 0 else "PARTIAL"

        if self.on_fill:
            try:
                self.on_fill(pos, level, lots, price)
            except Exception as exc:
                print(f"[paper_trader] on_fill callback failed (not fatal): {exc}")

    def _append_log(self, fill: Fill):
        with open(self.log_file, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(asdict(fill).keys()))
            writer.writerow(asdict(fill))

    def summary(self) -> dict:
        all_fills = [f for pos in self.open_positions for f in pos.fills]
        closed_positions = [p for p in self.open_positions if p.status == "CLOSED"]
        wins = [p for p in closed_positions if sum(f.pnl for f in p.fills) > 0]
        return {
            "capital": round(self.capital, 2),
            "positions_closed": len(closed_positions),
            "wins": len(wins),
            "win_rate_pct": round(100 * len(wins) / len(closed_positions), 1) if closed_positions else 0.0,
            "total_pnl": round(sum(f.pnl for f in all_fills), 2),
            "open_or_partial": len([p for p in self.open_positions if p.status != "CLOSED"]),
            "fills_logged": len(all_fills),
        }
