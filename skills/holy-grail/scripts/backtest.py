#!/usr/bin/env python3
"""
Holy Grail 백테스트 (Street Smarts Ch.10)

워크포워드 방식:
  - 심볼별 Yahoo Finance 일봉 데이터 (~2년)
  - 각 바에서 detect_setup() 실행
  - 셋업 감지 → 익일 open에 진입
  - 청산: stop_loss 터치(당일 low ≤ stop) 또는 max_hold 거래일 경과
  - 성과: core.backtest_core 표준 지표 + Monte Carlo p-value

Usage:
  python3 backtest.py                          # 기본 심볼
  python3 backtest.py --symbols QQQ,SPY,TQQQ   # 심볼 지정
  python3 backtest.py --period 2y --hold 10    # 기간/보유일 조정
  python3 backtest.py --json                   # JSON 출력
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).parent))  # scanner.py 직접 임포트용
from core.backtest_core import monte_carlo_pvalue, summary, walk_forward
from scanner import DEFAULT_SYMBOLS, detect_setup, get_ohlcv  # type: ignore


# ---------------------------------------------------------------------------
# Walk-forward backtest engine
# ---------------------------------------------------------------------------

def backtest_symbol(
    symbol: str,
    period: str = "2y",
    max_hold: int = 10,
    fee_pct: float = 0.001,
) -> dict:
    """단일 심볼 Holy Grail 워크포워드 백테스트.

    Returns dict with keys: symbol, trades, returns, metrics, walk_forward
    """
    try:
        bars = get_ohlcv(symbol, period=period, interval="1d")
    except Exception as e:
        return {"symbol": symbol, "error": str(e), "trades": []}

    if len(bars) < 80:
        return {"symbol": symbol, "error": "데이터 부족", "trades": []}

    trades = []
    returns = []
    warmup = 60  # ADX/EMA 안정화에 필요한 최소 봉 수

    i = warmup
    while i < len(bars) - 1:
        setup = detect_setup(bars[:i + 1])
        if setup.get("setup_active"):
            # 익일 open 진입
            entry_idx = i + 1
            if entry_idx >= len(bars):
                break
            entry_price = bars[entry_idx]["open"]
            stop_loss = setup["stop_loss"]

            # 청산: stop 터치 or max_hold 도달
            exit_price = None
            exit_idx = None
            exit_reason = "time"

            for j in range(entry_idx, min(entry_idx + max_hold, len(bars))):
                if bars[j]["low"] <= stop_loss:
                    exit_price = stop_loss
                    exit_idx = j
                    exit_reason = "stop"
                    break

            if exit_price is None:
                close_j = min(entry_idx + max_hold - 1, len(bars) - 1)
                exit_price = bars[close_j]["close"]
                exit_idx = close_j

            ret = (exit_price - entry_price) / entry_price * 100 - fee_pct * 100
            returns.append(ret)

            trades.append({
                "entry_date":  bars[entry_idx]["ts"],
                "exit_date":   bars[exit_idx]["ts"],
                "entry_price": round(entry_price, 4),
                "stop_loss":   round(stop_loss, 4),
                "exit_price":  round(exit_price, 4),
                "exit_reason": exit_reason,
                "ret_pct":     round(ret, 3),
            })

            # 해당 시그널 소비 — 다음 신규 셋업까지 3봉 스킵 (오버트레이딩 방지)
            i = exit_idx + 3
        else:
            i += 1

    if not returns:
        return {"symbol": symbol, "trades": [], "returns": [], "metrics": {}}

    metrics = summary(returns, hold_h=max_hold * 6)  # 일봉 × 6h ≈ 거래시간
    metrics["monte_carlo_pvalue"] = round(monte_carlo_pvalue(returns), 4)

    # 워크포워드: 전체를 4구간으로 분할
    wf_result = walk_forward(returns, n_windows=4)
    wf_windows = wf_result.get("windows", []) if isinstance(wf_result, dict) else []

    return {
        "symbol":           symbol,
        "total_bars":       len(bars),
        "trades":           trades,
        "returns":          returns,
        "metrics":          metrics,
        "walk_forward":     wf_windows,
        "wf_consistent":    wf_result.get("consistent", "") if isinstance(wf_result, dict) else "",
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(result: dict) -> None:
    sym = result["symbol"]
    if "error" in result:
        print(f"\n  ⚠  {sym}: {result['error']}")
        return

    m = result.get("metrics", {})
    trades = result.get("trades", [])
    wf = result.get("walk_forward", [])

    print(f"\n{'─' * 60}")
    print(f"  📊 {sym}  ({result.get('total_bars', 0)}봉 / {len(trades)}거래)")
    print(f"{'─' * 60}")

    if not trades:
        print("  셋업 감지 없음")
        return

    p_val = m.get("monte_carlo_pvalue", 1.0)
    significance = "✅ 유의" if p_val < 0.05 else ("⚠ 경계" if p_val < 0.15 else "❌ 비유의")
    print(f"  승률      : {m.get('win_rate', 0):.1f}%")
    print(f"  평균 수익 : {m.get('avg_ret_pct', 0):+.3f}%")
    print(f"  샤프 비율 : {m.get('sharpe', 0):.2f}")
    print(f"  최대 낙폭 : {m.get('max_drawdown', 0):.2f}%")
    print(f"  총 수익   : {m.get('total_return', 0):+.2f}%")
    print(f"  Monte Carlo p-value: {p_val:.4f}  {significance}")

    wf = result.get("walk_forward", [])
    wf_consistent = result.get("wf_consistent", "")
    if wf:
        print(f"\n  Walk-Forward 구간별 ({wf_consistent} 수익 구간):")
        for w in wf:
            bar = "█" * max(1, int(w.get("win_rate", 0) / 10))
            print(f"    구간 {w.get('window')}: {w.get('win_rate', 0):.1f}% {bar}")

    print(f"\n  최근 거래 5건:")
    for t in trades[-5:]:
        emoji = "✅" if t["ret_pct"] > 0 else "❌"
        from datetime import datetime
        try:
            d = datetime.utcfromtimestamp(t["entry_date"]).strftime("%m/%d")
        except Exception:
            d = str(t["entry_date"])[:10]
        print(
            f"    {emoji} {d}  진입={t['entry_price']:.2f}  "
            f"청산={t['exit_price']:.2f} ({t['exit_reason']})  "
            f"{t['ret_pct']:+.2f}%"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Holy Grail 백테스트")
    parser.add_argument("--symbols", help="쉼표 구분 심볼 (기본: QQQ,SPY,TIGER,KODEX200)")
    parser.add_argument("--period", default="2y", help="데이터 기간 (기본: 2y)")
    parser.add_argument("--hold", type=int, default=10, help="최대 보유 거래일 (기본: 10)")
    parser.add_argument("--json", action="store_true", dest="json_out", help="JSON 출력")
    args = parser.parse_args()

    if args.symbols:
        syms = [{"symbol": s, "label": s} for s in args.symbols.split(",")]
    else:
        syms = DEFAULT_SYMBOLS

    all_results = []
    for entry in syms:
        sym = entry["symbol"]
        print(f"[{sym}] 백테스트 중...", flush=True)
        result = backtest_symbol(sym, period=args.period, max_hold=args.hold)
        all_results.append(result)
        if not args.json_out:
            print_report(result)
        time.sleep(0.5)

    if args.json_out:
        print(json.dumps(all_results, ensure_ascii=False, indent=2))
    else:
        # 통합 요약
        all_rets = [r for res in all_results for r in res.get("returns", [])]
        if all_rets:
            m = summary(all_rets)
            m["monte_carlo_pvalue"] = round(monte_carlo_pvalue(all_rets), 4)
            print(f"\n{'═' * 60}")
            print(f"  전체 통합 성과 ({len(all_rets)}거래)")
            print(f"{'═' * 60}")
            print(f"  승률      : {m['win_rate']:.1f}%")
            print(f"  평균 수익 : {m['avg_ret_pct']:+.3f}%")
            print(f"  샤프 비율 : {m['sharpe']:.2f}")
            print(f"  최대 낙폭 : {m['max_drawdown']:.2f}%")
            print(f"  Monte Carlo p-value: {m['monte_carlo_pvalue']:.4f}")


if __name__ == "__main__":
    main()
