"""
범용 백테스트 러너.

모든 스킬/전략에서 동일한 인터페이스를 사용한다:
  detect_signal(bars: list[dict]) -> dict | None

사용법:
  from core.backtest_runner import run, run_from_file

  # 함수 직접 전달
  result = run(detect_signal_fn, symbols=["QQQ", "SPY"])

  # 파일 경로로 동적 로드
  result = run_from_file("skills/custom/my-strat/strategy.py", symbols=["QQQ"])
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional

_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(_ROOT))

from core.backtest_core import monte_carlo_pvalue, summary, walk_forward


# ── data fetcher ─────────────────────────────────────────────────────────────

def _default_ohlcv(symbol: str, period: str = "2y", interval: str = "1d") -> list[dict]:
    """Yahoo Finance daily bars (stdlib)."""
    import json
    import urllib.request

    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol}?range={period}&interval={interval}"
    )
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (backtest-runner/1.0)",
                      "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.load(r)

    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    q = result["indicators"]["quote"][0]
    bars = []
    for i, ts in enumerate(timestamps):
        o, h, lo, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, lo, c):
            continue
        bars.append({"ts": ts, "open": o, "high": h, "low": lo, "close": c})
    return bars


# ── core backtest logic ───────────────────────────────────────────────────────

def _run_symbol(
    symbol: str,
    detect_fn: Callable,
    warmup: int,
    max_hold: int,
    fee_pct: float,
    period: str,
    data_fn: Optional[Callable] = None,
) -> dict:
    fetch = data_fn or _default_ohlcv
    try:
        bars = fetch(symbol, period, "1d")
    except Exception as e:
        return {"symbol": symbol, "error": str(e), "trades": [], "returns": []}

    if len(bars) < warmup + 5:
        return {"symbol": symbol, "error": "데이터 부족", "trades": [], "returns": []}

    trades: list[dict] = []
    returns: list[float] = []
    i = warmup

    while i < len(bars) - 1:
        try:
            sig = detect_fn(bars[: i + 1])
        except Exception:
            i += 1
            continue

        if not sig or not sig.get("active"):
            i += 1
            continue

        entry_idx = i + 1
        if entry_idx >= len(bars):
            break

        entry = sig.get("entry_price") or bars[entry_idx]["open"]
        stop = sig.get("stop_loss", entry * 0.95)

        if stop >= entry:  # invalid stop
            i += 1
            continue

        exit_price, exit_reason = None, "time"
        for j in range(entry_idx, min(entry_idx + max_hold, len(bars))):
            if bars[j]["low"] <= stop:
                exit_price = stop
                exit_reason = "stop"
                break

        if exit_price is None:
            j = min(entry_idx + max_hold - 1, len(bars) - 1)
            exit_price = bars[j]["close"]

        ret = (exit_price - entry) / entry * 100 - fee_pct * 100
        returns.append(ret)

        from datetime import datetime, timezone
        try:
            d_entry = datetime.fromtimestamp(bars[entry_idx]["ts"], tz=timezone.utc).strftime("%Y-%m-%d")
            d_exit = datetime.fromtimestamp(bars[j]["ts"], tz=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            d_entry = str(bars[entry_idx]["ts"])
            d_exit = str(bars[j]["ts"])

        trades.append({
            "symbol":       symbol,
            "entry_date":   d_entry,
            "exit_date":    d_exit,
            "entry_price":  round(entry, 4),
            "stop_loss":    round(stop, 4),
            "exit_price":   round(exit_price, 4),
            "exit_reason":  exit_reason,
            "ret_pct":      round(ret, 3),
            "reason":       sig.get("reason", ""),
        })
        i = j + 3  # overkill prevention

    if not returns:
        return {"symbol": symbol, "trades": [], "returns": [], "metrics": {}}

    m = summary(returns, hold_h=max_hold * 6)
    m["monte_carlo_pvalue"] = round(monte_carlo_pvalue(returns), 4)
    wf = walk_forward(returns, n_windows=min(4, max(2, len(returns) // 5)))
    wf_windows = wf.get("windows", []) if isinstance(wf, dict) else []
    wf_consistent = wf.get("consistent", "") if isinstance(wf, dict) else ""

    return {
        "symbol":        symbol,
        "total_bars":    len(bars),
        "trades":        trades,
        "returns":       returns,
        "metrics":       m,
        "walk_forward":  wf_windows,
        "wf_consistent": wf_consistent,
    }


# ── public API ────────────────────────────────────────────────────────────────

def run(
    detect_fn: Callable,
    symbols: List[str],
    period: str = "2y",
    max_hold: int = 10,
    fee_pct: float = 0.001,
    warmup: int = 60,
    data_fn: Optional[Callable] = None,
) -> dict:
    """범용 백테스트 실행.

    Args:
        detect_fn:  detect_signal(bars) -> dict | None
        symbols:    심볼 리스트 (Yahoo Finance 티커)
        period:     데이터 기간 ('1y', '2y', '5y')
        max_hold:   최대 보유 거래일
        fee_pct:    편도 수수료 (기본 0.1%)
        warmup:     신호 생성 전 최소 봉 수
        data_fn:    커스텀 데이터 fetch 함수 (None=Yahoo Finance)

    Returns:
        {
            "symbols": [per-symbol result dict, ...],
            "aggregate": {metrics, monte_carlo_pvalue, ...},
        }
    """
    per_symbol = []
    for sym in symbols:
        result = _run_symbol(sym, detect_fn, warmup, max_hold, fee_pct, period, data_fn)
        per_symbol.append(result)
        time.sleep(0.5)

    all_rets = [r for res in per_symbol for r in res.get("returns", [])]
    if all_rets:
        agg = summary(all_rets)
        agg["monte_carlo_pvalue"] = round(monte_carlo_pvalue(all_rets), 4)
        wf = walk_forward(all_rets, n_windows=4)
        agg["wf_consistent"] = wf.get("consistent", "") if isinstance(wf, dict) else ""
    else:
        agg = {}

    return {"symbols": per_symbol, "aggregate": agg}


def run_from_file(
    strategy_path: str | Path,
    symbols: Optional[List[str]] = None,
    **kwargs,
) -> dict:
    """전략 파일을 동적으로 로드하여 백테스트 실행.

    strategy_path 모듈에서 detect_signal 함수와 METADATA dict를 읽는다.
    symbols를 명시하지 않으면 METADATA['symbols']를 사용한다.
    """
    path = Path(strategy_path)
    spec = importlib.util.spec_from_file_location("_strategy_module", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"전략 파일 로드 실패: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    detect_fn = getattr(mod, "detect_signal", None)
    if not callable(detect_fn):
        raise AttributeError(f"{path}에 detect_signal 함수가 없습니다")

    metadata: dict = getattr(mod, "METADATA", {})
    syms = symbols or metadata.get("symbols", ["QQQ", "SPY"])
    max_hold = kwargs.pop("max_hold", metadata.get("hold_days", 10))
    warmup = kwargs.pop("warmup", metadata.get("warmup_bars", 60))

    return run(detect_fn, syms, max_hold=max_hold, warmup=warmup, **kwargs)


def format_report(result: dict, include_trades: bool = False) -> str:
    """백테스트 결과를 텔레그램 친화적 텍스트로 포맷."""
    lines: list[str] = []

    for r in result.get("symbols", []):
        sym = r["symbol"]
        if "error" in r:
            lines.append(f"⚠ {sym}: {r['error']}")
            continue
        m = r.get("metrics", {})
        trades = r.get("trades", [])
        wf_c = r.get("wf_consistent", "")
        p_val = m.get("monte_carlo_pvalue", 1.0)
        sig = "✅" if p_val < 0.05 else ("⚠" if p_val < 0.15 else "❌")
        lines.append(
            f"<b>{sym}</b> {len(trades)}거래\n"
            f"  승률 {m.get('win_rate', 0):.1f}%  "
            f"평균 {m.get('avg_ret_pct', 0):+.3f}%  "
            f"샤프 {m.get('sharpe', 0):.2f}\n"
            f"  최대낙폭 {m.get('max_drawdown', 0):.2f}%  "
            f"WF {wf_c}  MC p={p_val:.3f} {sig}"
        )
        if include_trades and trades:
            for t in trades[-3:]:
                e = "✅" if t["ret_pct"] > 0 else "❌"
                lines.append(
                    f"  {e} {t['entry_date']} → {t['exit_date']}  "
                    f"{t['ret_pct']:+.2f}% ({t['exit_reason']})"
                )

    agg = result.get("aggregate", {})
    if agg:
        p_val = agg.get("monte_carlo_pvalue", 1.0)
        sig = "✅" if p_val < 0.05 else ("⚠" if p_val < 0.15 else "❌")
        lines.append(
            f"\n<b>전체 통합</b>\n"
            f"  승률 {agg.get('win_rate', 0):.1f}%  "
            f"평균 {agg.get('avg_ret_pct', 0):+.3f}%  "
            f"샤프 {agg.get('sharpe', 0):.2f}\n"
            f"  MC p={p_val:.3f} {sig}  WF {agg.get('wf_consistent', '')}"
        )

    return "\n".join(lines)
