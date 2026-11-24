"""
backtest_report.py
------------------
Generates a self-contained HTML dashboard so you can SEE, on a price chart,
where every historical signal would have fired and whether it would have
hit target or stoploss. Opens directly in any browser -- no server needed.

Usage:
    python backtest_report.py --mock     # synthetic data, works offline
    python backtest_report.py            # real NSE data (run on your own machine)

Output: backtest_report.html in this folder.

IMPORTANT: like backtest.py, this approximates option premium moves from
underlying price moves using a fixed delta/leverage assumption (see
backtest.py docstring) because free historical option-chain data isn't
available. Treat the win-rate here as a directional-edge sanity check, not
a promise of real premium performance -- paper trading (main.py --mode
paper, run daily for a few weeks) is the real validation step.
"""

import argparse
import json

import config
from data_fetcher import get_provider
import backtest as bt


def build_dataset(provider, symbols_with_index):
    price_series = {}
    all_trades = []
    consecutive_failures = 0

    for symbol, is_index in symbols_with_index:
        try:
            snap = provider.get_underlying_snapshot(symbol, is_index=is_index)
            consecutive_failures = 0
        except Exception as exc:
            print(f"[backtest_report] skip {symbol}: {exc}")
            consecutive_failures += 1
            if consecutive_failures >= 5:
                print(
                    "[backtest_report] 5 symbols in a row failed - the data feed is "
                    "down. Stopping early. Run diagnose_angelone.py to check the broker feed."
                )
                break
            continue

        dates = snap.history.get("CH_TIMESTAMP", None)
        closes = snap.history.get("CH_CLOSING_PRICE", None)
        if dates is None or closes is None:
            continue

        price_series[symbol] = {
            "dates": [str(d) for d in dates.tolist()],
            "close": [round(float(c), 2) if c == c else None for c in closes.tolist()],
        }

        records = bt.backtest_detailed(symbol, snap.history)
        for rec in records:
            all_trades.append({
                "symbol": rec.symbol,
                "entry_date": rec.entry_date,
                "exit_date": rec.exit_date,
                "direction": rec.direction,
                "entry_underlying": rec.entry_underlying,
                "exit_underlying": rec.exit_underlying,
                "outcome": rec.outcome,
                "pnl_pct": rec.pnl_pct,
                "reasons": "; ".join(rec.reasons) if rec.reasons else "momentum signal only",
            })

    return price_series, all_trades


def compute_kpis(trades: list) -> dict:
    if not trades:
        return {
            "total_signals": 0, "win_rate_pct": 0, "avg_return_pct": 0,
            "best_symbol": "-", "worst_symbol": "-",
        }
    wins = [t for t in trades if t["pnl_pct"] > 0]
    per_symbol = {}
    for t in trades:
        per_symbol.setdefault(t["symbol"], []).append(t["pnl_pct"])
    per_symbol_avg = {s: sum(v) / len(v) for s, v in per_symbol.items()}
    best_symbol = max(per_symbol_avg, key=per_symbol_avg.get)
    worst_symbol = min(per_symbol_avg, key=per_symbol_avg.get)
    return {
        "total_signals": len(trades),
        "win_rate_pct": round(100 * len(wins) / len(trades), 1),
        "avg_return_pct": round(sum(t["pnl_pct"] for t in trades) / len(trades), 2),
        "best_symbol": f"{best_symbol} ({per_symbol_avg[best_symbol]:.1f}% avg)",
        "worst_symbol": f"{worst_symbol} ({per_symbol_avg[worst_symbol]:.1f}% avg)",
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Signal Backtest Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.1"></script>
<style>
:root {
  --bg-primary:#f8f9fa; --bg-card:#fff; --bg-header:#1a1a2e;
  --text-primary:#212529; --text-secondary:#6c757d; --text-on-dark:#fff;
  --positive:#28a745; --negative:#dc3545; --neutral:#6c757d;
  --gap:16px; --radius:8px;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
       background:var(--bg-primary); color:var(--text-primary); line-height:1.5; }
.container { max-width:1300px; margin:0 auto; padding:var(--gap); }
header { background:var(--bg-header); color:var(--text-on-dark); padding:20px 24px;
         border-radius:var(--radius); margin-bottom:var(--gap); display:flex;
         justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; }
header h1 { font-size:20px; font-weight:600; }
header p { font-size:12px; color:rgba(255,255,255,0.7); margin-top:4px; max-width:640px; }
.filters select { padding:6px 10px; border-radius:4px; border:1px solid rgba(255,255,255,0.2);
                  background:rgba(255,255,255,0.12); color:#fff; font-size:13px; }
.kpi-row { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
           gap:var(--gap); margin-bottom:var(--gap); }
.kpi-card { background:var(--bg-card); border-radius:var(--radius); padding:18px 20px;
            box-shadow:0 1px 3px rgba(0,0,0,0.08); }
.kpi-label { font-size:12px; color:var(--text-secondary); text-transform:uppercase;
             letter-spacing:0.5px; margin-bottom:4px; }
.kpi-value { font-size:24px; font-weight:700; }
.chart-card { background:var(--bg-card); border-radius:var(--radius); padding:20px 24px;
              box-shadow:0 1px 3px rgba(0,0,0,0.08); margin-bottom:var(--gap); }
.chart-card h3 { font-size:14px; margin-bottom:12px; }
.chart-card canvas { max-height:420px; }
.legend-note { font-size:12px; color:var(--text-secondary); margin-top:8px; }
.table-card { background:var(--bg-card); border-radius:var(--radius); padding:20px 24px;
              box-shadow:0 1px 3px rgba(0,0,0,0.08); overflow-x:auto; }
table { width:100%; border-collapse:collapse; font-size:13px; }
thead th { text-align:left; padding:8px 10px; border-bottom:2px solid #dee2e6;
           color:var(--text-secondary); font-weight:600; font-size:11px; text-transform:uppercase;
           cursor:pointer; white-space:nowrap; }
tbody td { padding:8px 10px; border-bottom:1px solid #f0f0f0; }
tbody tr:hover { background:#f8f9fa; }
.tag { padding:2px 8px; border-radius:10px; font-size:11px; font-weight:600; }
.tag.win { background:#d4edda; color:#155724; }
.tag.loss { background:#f8d7da; color:#721c24; }
footer { text-align:center; font-size:12px; color:var(--text-secondary); padding:16px 0; }
</style>
</head>
<body>
<div class="container">
  <header>
    <div>
      <h1>Signal Backtest Dashboard</h1>
      <p>Directional momentum signal replayed on historical closes, with an approximated
      option-premium outcome (fixed delta/leverage assumption). This is a sanity check on
      whether the rules have any edge -- not a promise of live performance.</p>
    </div>
    <div class="filters">
      <select id="symbolSelect" onchange="onSymbolChange()"></select>
    </div>
  </header>

  <section class="kpi-row">
    <div class="kpi-card"><div class="kpi-label">Total Signals</div><div class="kpi-value" id="kpiTotal">-</div></div>
    <div class="kpi-card"><div class="kpi-label">Win Rate</div><div class="kpi-value" id="kpiWinRate">-</div></div>
    <div class="kpi-card"><div class="kpi-label">Avg Return / Trade</div><div class="kpi-value" id="kpiAvgReturn">-</div></div>
    <div class="kpi-card"><div class="kpi-label">Best Symbol</div><div class="kpi-value" id="kpiBest" style="font-size:15px;">-</div></div>
    <div class="kpi-card"><div class="kpi-label">Worst Symbol</div><div class="kpi-value" id="kpiWorst" style="font-size:15px;">-</div></div>
  </section>

  <section class="chart-card">
    <h3 id="chartTitle">Price chart</h3>
    <canvas id="priceChart"></canvas>
    <div class="legend-note">▲ blue = bullish entry &nbsp; ▼ orange = bearish entry &nbsp;
      ● green = winning exit &nbsp; ● red = losing exit</div>
  </section>

  <section class="table-card">
    <h3 style="margin-bottom:12px;">All historical signals</h3>
    <table id="tradeTable">
      <thead><tr>
        <th onclick="sortBy('symbol')">Symbol</th>
        <th onclick="sortBy('entry_date')">Entry Date</th>
        <th onclick="sortBy('direction')">Direction</th>
        <th onclick="sortBy('entry_underlying')">Entry Px</th>
        <th onclick="sortBy('exit_date')">Exit Date</th>
        <th onclick="sortBy('exit_underlying')">Exit Px</th>
        <th onclick="sortBy('outcome')">Outcome</th>
        <th onclick="sortBy('pnl_pct')">Approx PnL%</th>
        <th>Reason</th>
      </tr></thead>
      <tbody id="tradeTableBody"></tbody>
    </table>
  </section>

  <footer>Generated by backtest_report.py &middot; approximated option-premium outcomes, not real historical option prices</footer>
</div>

<script>
const PRICE_SERIES = __PRICE_SERIES__;
const TRADES = __TRADES__;
const KPIS = __KPIS__;

document.getElementById('kpiTotal').textContent = KPIS.total_signals;
document.getElementById('kpiWinRate').textContent = KPIS.win_rate_pct + '%';
document.getElementById('kpiAvgReturn').textContent = KPIS.avg_return_pct + '%';
document.getElementById('kpiBest').textContent = KPIS.best_symbol;
document.getElementById('kpiWorst').textContent = KPIS.worst_symbol;

const symbolSelect = document.getElementById('symbolSelect');
Object.keys(PRICE_SERIES).sort().forEach(sym => {
  const opt = document.createElement('option');
  opt.value = sym; opt.textContent = sym;
  symbolSelect.appendChild(opt);
});

let chart = null;
let sortState = { col: 'pnl_pct', dir: 'desc' };

function buildMarkers(symbol) {
  const trades = TRADES.filter(t => t.symbol === symbol);
  const entries = trades.map(t => ({ x: t.entry_date, y: t.entry_underlying, meta: t }));
  const exits = trades.map(t => ({ x: t.exit_date, y: t.exit_underlying, meta: t }));
  return { trades, entries, exits };
}

function renderChart(symbol) {
  const series = PRICE_SERIES[symbol];
  const { entries, exits } = buildMarkers(symbol);
  document.getElementById('chartTitle').textContent = symbol + ' -- close price with signal markers';

  const entryBull = entries.filter(e => e.meta.direction === 'BULLISH');
  const entryBear = entries.filter(e => e.meta.direction === 'BEARISH');
  const exitWin = exits.filter(e => e.meta.pnl_pct > 0);
  const exitLoss = exits.filter(e => e.meta.pnl_pct <= 0);

  const datasets = [
    { type: 'line', label: 'Close', data: series.dates.map((d,i) => ({x:d, y:series.close[i]})),
      borderColor:'#4C72B0', backgroundColor:'transparent', borderWidth:1.5, pointRadius:0, tension:0.15 },
    { type: 'scatter', label: 'Bullish entry', data: entryBull, backgroundColor:'#1f77b4',
      pointStyle:'triangle', pointRadius:7, rotation:0 },
    { type: 'scatter', label: 'Bearish entry', data: entryBear, backgroundColor:'#ff7f0e',
      pointStyle:'triangle', pointRadius:7, rotation:180 },
    { type: 'scatter', label: 'Win exit', data: exitWin, backgroundColor:'#28a745', pointRadius:6 },
    { type: 'scatter', label: 'Loss exit', data: exitLoss, backgroundColor:'#dc3545', pointRadius:6 },
  ];

  if (chart) chart.destroy();
  const ctx = document.getElementById('priceChart').getContext('2d');
  chart = new Chart(ctx, {
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      parsing: false,
      scales: {
        x: { type: 'category', labels: series.dates, ticks: { maxTicksLimit: 12 } },
        y: { beginAtZero: false }
      },
      plugins: {
        legend: { position: 'top' },
        tooltip: {
          callbacks: {
            label: function(ctx) {
              const m = ctx.raw.meta;
              if (!m) return `Close: ${ctx.parsed.y}`;
              return `${m.direction} | entry ${m.entry_underlying} -> exit ${m.exit_underlying} | ${m.outcome} | ${m.pnl_pct}%`;
            }
          }
        }
      }
    }
  });
}

function renderTable() {
  const sorted = [...TRADES].sort((a,b) => {
    const av = a[sortState.col], bv = b[sortState.col];
    const cmp = av < bv ? -1 : av > bv ? 1 : 0;
    return sortState.dir === 'asc' ? cmp : -cmp;
  });
  const body = document.getElementById('tradeTableBody');
  body.innerHTML = sorted.slice(0, 300).map(t => `
    <tr>
      <td>${t.symbol}</td><td>${t.entry_date}</td><td>${t.direction}</td>
      <td>${t.entry_underlying}</td><td>${t.exit_date}</td><td>${t.exit_underlying}</td>
      <td>${t.outcome}</td>
      <td><span class="tag ${t.pnl_pct > 0 ? 'win' : 'loss'}">${t.pnl_pct}%</span></td>
      <td>${t.reasons}</td>
    </tr>`).join('');
}

window.sortBy = function(col) {
  sortState.dir = (sortState.col === col && sortState.dir === 'desc') ? 'asc' : 'desc';
  sortState.col = col;
  renderTable();
};

window.onSymbolChange = function() {
  renderChart(symbolSelect.value);
};

if (Object.keys(PRICE_SERIES).length) {
  symbolSelect.value = Object.keys(PRICE_SERIES).sort()[0];
  renderChart(symbolSelect.value);
}
renderTable();
</script>
</body>
</html>
"""


def write_report(price_series: dict, trades: list, out_path: str = "backtest_report.html"):
    kpis = compute_kpis(trades)
    html = (HTML_TEMPLATE
            .replace("__PRICE_SERIES__", json.dumps(price_series))
            .replace("__TRADES__", json.dumps(trades))
            .replace("__KPIS__", json.dumps(kpis)))
    with open(out_path, "w") as f:
        f.write(html)
    print(f"[backtest_report] wrote {out_path} ({len(trades)} historical signals across {len(price_series)} symbols)")
    print(f"[backtest_report] KPIs: {kpis}")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true", help="force synthetic data, skip live feeds")
    parser.add_argument("--broker", choices=["nse", "angelone"], default="nse")
    parser.add_argument("--out", default="backtest_report.html")
    args = parser.parse_args()

    provider = get_provider(prefer_live=not args.mock, broker=args.broker)
    universe = [(s, True) for s in config.INDEX_UNIVERSE] + [(s, False) for s in config.STOCK_UNIVERSE]
    price_series, trades = build_dataset(provider, universe)
    write_report(price_series, trades, args.out)
