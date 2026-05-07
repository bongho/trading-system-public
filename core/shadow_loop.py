"""
Shadow Account Loop.

Reads JSONL simulation logs produced by simulate_upbit.py,
extracts winning trade patterns, and suggests regime_params adjustments.

Inspired by Vibe-Trading's "Shadow Account" feature which reverse-engineers
implicit trading rules from actual trade history.

Log entry format (from simulate_upbit.py):
  {
    "ts": "2026-05-07T...",
    "recs": [{"market": "KRW-BTC", "rsi_14": 52, "vol_ratio": 2.1,
              "change_1h": 1.3, "change_15m": 0.4, "entry_price": 95000000, ...}],
    "prev_pnl": {"KRW-BTC": 1.2, "KRW-ETH": -0.5}  # P&L of previous round's recs
  }
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class TradeRecord:
    market: str
    rsi: float
    vol_ratio: float
    ch_1h: float
    ch_15m: float
    pnl: float
    ts: str


@dataclass
class ShadowAnalysis:
    status: str                          # 'analyzed' | 'insufficient_data'
    total_trades: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    win_rsi_range: list[float] = field(default_factory=list)
    loss_rsi_range: list[float] = field(default_factory=list)
    win_vol_range: list[float] = field(default_factory=list)
    suggested_params: dict = field(default_factory=dict)
    current_params: dict = field(default_factory=dict)
    top_win_markets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'status':           self.status,
            'total_trades':     self.total_trades,
            'win_count':        self.win_count,
            'loss_count':       self.loss_count,
            'win_rate':         self.win_rate,
            'win_rsi_range':    self.win_rsi_range,
            'loss_rsi_range':   self.loss_rsi_range,
            'win_vol_range':    self.win_vol_range,
            'suggested_params': self.suggested_params,
            'current_params':   self.current_params,
            'top_win_markets':  self.top_win_markets,
        }

    def summary_text(self) -> str:
        if self.status == 'insufficient_data':
            return f"[Shadow Loop] 데이터 부족 ({self.total_trades}건)"
        lines = [
            f"[Shadow Loop] {self.total_trades}건 분석 | 승률 {self.win_rate:.1f}%",
            f"  승리 RSI 범위: {self.win_rsi_range}",
            f"  승리 Vol 범위: {self.win_vol_range}",
            f"  제안 파라미터: {self.suggested_params}",
        ]
        return "\n".join(lines)


def _load_entries(log_dir: Path) -> list[dict]:
    entries = []
    for f in sorted(log_dir.glob("*.jsonl")):
        with open(f, encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    return entries


def _pair_trades(entries: list[dict]) -> list[TradeRecord]:
    """
    Each entry has recs (current round) and prev_pnl (P&L of prev round's recs).
    To pair signal → outcome, match entry[i-1].recs with entry[i].prev_pnl.
    """
    records = []
    for i in range(1, len(entries)):
        prev_recs = entries[i - 1].get('recs', [])
        curr_pnl  = entries[i].get('prev_pnl', {})

        if not prev_recs or not curr_pnl:
            continue

        for rec in prev_recs:
            market = rec.get('market')
            if not market:
                continue
            pnl = curr_pnl.get(market)
            if pnl is None:
                continue

            records.append(TradeRecord(
                market    = market,
                rsi       = float(rec.get('rsi_14', 50)),
                vol_ratio = float(rec.get('vol_ratio', 1.0)),
                ch_1h     = float(rec.get('change_1h', 0)),
                ch_15m    = float(rec.get('change_15m', 0)),
                pnl       = float(pnl),
                ts        = entries[i - 1].get('ts', ''),
            ))
    return records


def analyze(log_dir: Path, min_trades: int = 10) -> ShadowAnalysis:
    """Read logs and extract winning patterns."""
    entries = _load_entries(log_dir)
    records = _pair_trades(entries)

    if len(records) < min_trades:
        return ShadowAnalysis(status='insufficient_data', total_trades=len(records))

    wins   = [r for r in records if r.pnl > 0]
    losses = [r for r in records if r.pnl <= 0]

    win_rate   = round(len(wins) / len(records) * 100, 1)
    win_rsi    = sorted(r.rsi for r in wins)
    loss_rsi   = sorted(r.rsi for r in losses)
    win_vol    = sorted(r.vol_ratio for r in wins)

    # Suggested params: center ±σ of winning trades
    def _range(vals: list[float]) -> list[float]:
        if not vals:
            return []
        return [round(vals[0], 1), round(vals[-1], 1)]

    # Trim top/bottom 10% outliers before suggesting range
    def _trim(vals: list[float], pct: float = 0.10) -> list[float]:
        n = len(vals)
        cut = max(1, int(n * pct))
        return vals[cut:-cut] if n > 2 * cut else vals

    trimmed_rsi = _trim(win_rsi)
    trimmed_vol = _trim(win_vol)

    suggested = {}
    if trimmed_rsi:
        suggested['rsi_lo']       = max(40, int(trimmed_rsi[0]) - 2)
        suggested['rsi_hi']       = min(75, int(trimmed_rsi[-1]) + 2)
    if trimmed_vol:
        suggested['vol_ratio_min'] = round(max(1.0, trimmed_vol[0] * 0.9), 1)

    # Top win markets by win count
    from collections import Counter
    market_wins = Counter(r.market for r in wins)
    top_win_markets = [m for m, _ in market_wins.most_common(5)]

    return ShadowAnalysis(
        status           = 'analyzed',
        total_trades     = len(records),
        win_count        = len(wins),
        loss_count       = len(losses),
        win_rate         = win_rate,
        win_rsi_range    = _range(win_rsi),
        loss_rsi_range   = _range(loss_rsi),
        win_vol_range    = _range(win_vol),
        suggested_params = suggested,
        current_params   = {'rsi_lo': 45, 'rsi_hi': 68, 'vol_ratio_min': 1.5},
        top_win_markets  = top_win_markets,
    )


if __name__ == '__main__':
    import sys
    log_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('skills/crypto-recommender/logs')
    result  = analyze(log_dir)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
