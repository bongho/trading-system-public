"""
Shared backtesting infrastructure for all trading skills.

Provides:
  summary()            — standard metrics dict
  monte_carlo_pvalue() — statistical edge validation (lower p = more significant)
  walk_forward()       — sequential window consistency check
  run_backtest()       — generic signal/forward return loop

Usage example (crypto):
    result = run_backtest(
        signal_fn=lambda idx, sym, rows: score_v3(...),
        forward_fn=lambda idx, sym, rows, hold: (rows[idx+hold][2] - rows[idx][2]) / rows[idx][2] * 100,
        data=candle_dict,
        hold=4, freq=4, top_n=3,
        fee=0.0004, slippage=0.0002,
    )
"""
import math
import random
from typing import Callable, List, Optional


# ── public API ─────────────────────────────────────────────────────────────────

def summary(rets: List[float], hold_h: int = 4) -> dict:
    """Compute standard performance metrics from a list of period returns (%)."""
    if not rets:
        return {}
    wins = sum(1 for r in rets if r > 0)
    avg  = sum(rets) / len(rets)
    # annualisation: assume hold_h-hour periods, 252 trading days
    periods_per_year = (24 / max(hold_h, 1)) * 252
    return {
        'signals':      len(rets),
        'win_rate':     round(wins / len(rets) * 100, 1),
        'avg_ret_pct':  round(avg, 3),
        'sharpe':       _sharpe(rets, periods_per_year),
        'max_drawdown': _max_dd(rets),
        'total_return': round(sum(rets), 2),
        'best':         round(max(rets), 2),
        'worst':        round(min(rets), 2),
    }


def monte_carlo_pvalue(rets: List[float], n: int = 1000) -> float:
    """P-value: fraction of random permutations with Sharpe ≥ actual Sharpe.

    Interpretation: < 0.05 means the strategy's edge is unlikely to be random.
    """
    if len(rets) < 2:
        return 1.0
    ppy = (24 / 4) * 252  # default 4h hold annualisation
    actual = _sharpe(rets, ppy)
    beats  = sum(
        1 for _ in range(n)
        if _sharpe(random.sample(rets, len(rets)), ppy) >= actual
    )
    return round(beats / n, 4)


def walk_forward(rets: List[float], n_windows: int = 5) -> dict:
    """Split returns into n sequential windows; report per-window profitability.

    A robust strategy should show positive avg_ret in most or all windows.
    'consistent' reports how many windows were profitable.
    """
    if len(rets) < n_windows * 5:
        return {'error': 'insufficient data for walk-forward'}
    size = len(rets) // n_windows
    windows = []
    for i in range(n_windows):
        window = rets[i * size:(i + 1) * size]
        s = summary(window)
        windows.append({'window': i + 1, **s})
    consistent = sum(1 for w in windows if w.get('avg_ret_pct', 0) > 0)
    return {
        'windows':    windows,
        'consistent': f'{consistent}/{n_windows}',
    }


def run_backtest(
    signal_fn: Callable,
    forward_fn: Callable,
    data: dict,
    hold: int = 4,
    freq: int = 4,
    top_n: int = 3,
    fee: float = 0.0004,
    slippage: float = 0.0002,
) -> dict:
    """Generic vectorised backtest loop.

    Args:
        signal_fn:  fn(idx, symbol, rows) → float score or None
        forward_fn: fn(idx, symbol, rows, hold) → forward return %
        data:       {symbol: list_of_rows}
        hold:       hold period in candles
        freq:       signal scanning frequency in candles
        top_n:      how many top-scored signals to take per period
        fee:        round-trip fee as decimal (0.0004 = 0.04%)
        slippage:   one-way slippage as decimal

    Returns:
        summary dict enriched with monte_carlo_pvalue and walk_forward results
    """
    symbols = list(data.keys())
    min_len = min(len(data[s]) for s in symbols)
    rets: List[float] = []

    for idx in range(21, min_len - hold - 1, freq):
        candidates = []
        for sym in symbols:
            rows = data[sym]
            if idx + hold >= len(rows):
                continue
            score = signal_fn(idx, sym, rows)
            if score is None:
                continue
            fwd = forward_fn(idx, sym, rows, hold)
            net = fwd - (fee + slippage) * 100  # convert decimal fee to pct
            candidates.append((score, net))

        top = sorted(candidates, reverse=True)[:top_n]
        if top:
            rets.append(sum(r for _, r in top) / len(top))

    result = summary(rets, hold)
    result['fee_pct']             = round((fee + slippage) * 100, 4)
    result['monte_carlo_pvalue']  = monte_carlo_pvalue(rets)
    result['walk_forward']        = walk_forward(rets)
    return result


# ── internal helpers ───────────────────────────────────────────────────────────

def _sharpe(rets: List[float], periods_per_year: float) -> float:
    if len(rets) < 2:
        return 0.0
    avg = sum(rets) / len(rets)
    std = math.sqrt(sum((r - avg) ** 2 for r in rets) / len(rets))
    return round(avg / std * math.sqrt(periods_per_year), 3) if std > 0 else 0.0


def _max_dd(rets: List[float]) -> float:
    cum = peak = dd = 0.0
    for r in rets:
        cum  += r
        peak  = max(peak, cum)
        dd    = max(dd, peak - cum)
    return round(dd, 2)
