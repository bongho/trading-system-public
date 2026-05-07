#!/usr/bin/env python3
# Holy Grail Scanner — Street Smarts (Raschke & Connors, Ch.10)
# Strategy: ADX(14) > 30 & rising + price pullback to 20EMA → buy stop
#
# Modes:
#   scan   — 기본 심볼 전체 스캔 (셋업 감지)
#   check  — 특정 심볼 상세 분석
#   watch  — 반복 감시 (interval 초마다)
#
# Data: Yahoo Finance chart API (stdlib only, no auth)

from __future__ import annotations
import argparse, json, time, urllib.request
from typing import Any

DEFAULT_SYMBOLS: list[dict[str, str]] = [
    {"symbol": "QQQ",      "label": "QQQ (NASDAQ-100)"},
    {"symbol": "SPY",      "label": "SPY (S&P 500)"},
    {"symbol": "360200.KS", "label": "TIGER 미국S&P500"},
    {"symbol": "069500.KS", "label": "KODEX 200"},
]

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range}&interval={interval}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (holy-grail-scanner/1.0)",
    "Accept": "application/json",
}

# 20EMA 터치 판정 임계값 (±1.5% 이내)
EMA_TOUCH_THRESHOLD = 0.015

# ADX 상승 판정: 최근 N봉 평균 기울기 양수
ADX_RISE_LOOKBACK = 3


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def fetch_json(url: str) -> Any:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def get_ohlcv(symbol: str, period: str = "6mo", interval: str = "1d") -> list[dict]:
    url = YAHOO_CHART.format(symbol=symbol, range=period, interval=interval)
    data = fetch_json(url)
    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    q = result["indicators"]["quote"][0]
    bars = []
    for i, ts in enumerate(timestamps):
        o = q["open"][i]
        h = q["high"][i]
        lo = q["low"][i]
        c = q["close"][i]
        if None in (o, h, lo, c):
            continue
        bars.append({"ts": ts, "open": o, "high": h, "low": lo, "close": c})
    return bars


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def calc_ema(values: list[float], period: int) -> list[float]:
    k = 2.0 / (period + 1)
    ema: list[float] = []
    for i, v in enumerate(values):
        if i < period - 1:
            ema.append(float("nan"))
        elif i == period - 1:
            ema.append(sum(values[:period]) / period)
        else:
            ema.append(v * k + ema[-1] * (1 - k))
    return ema


def calc_wilder_smooth(values: list[float], period: int) -> list[float]:
    """Wilder's smoothing (used for ATR, ADX)."""
    result: list[float] = [float("nan")] * len(values)
    # first valid = simple sum of first `period` non-nan values
    start = 0
    while start < len(values) and values[start] != values[start]:  # nan check
        start += 1
    if start + period > len(values):
        return result
    result[start + period - 1] = sum(values[start:start + period])
    for i in range(start + period, len(values)):
        result[i] = result[i - 1] - result[i - 1] / period + values[i]
    return result


def calc_adx(bars: list[dict], period: int = 14) -> dict[str, list[float]]:
    n = len(bars)
    tr_list: list[float] = [float("nan")]
    plus_dm: list[float] = [float("nan")]
    minus_dm: list[float] = [float("nan")]

    for i in range(1, n):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_list.append(tr)

        up_move = bars[i]["high"] - bars[i - 1]["high"]
        down_move = bars[i - 1]["low"] - bars[i]["low"]
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)

    atr_s = calc_wilder_smooth(tr_list, period)
    plus_s = calc_wilder_smooth(plus_dm, period)
    minus_s = calc_wilder_smooth(minus_dm, period)

    plus_di: list[float] = []
    minus_di: list[float] = []
    dx_list: list[float] = []

    for i in range(n):
        atr = atr_s[i]
        if atr != atr or atr == 0:  # nan or zero
            plus_di.append(float("nan"))
            minus_di.append(float("nan"))
            dx_list.append(float("nan"))
            continue
        pdi = 100.0 * plus_s[i] / atr
        mdi = 100.0 * minus_s[i] / atr
        plus_di.append(pdi)
        minus_di.append(mdi)
        denom = pdi + mdi
        dx_list.append(100.0 * abs(pdi - mdi) / denom if denom != 0 else 0.0)

    adx = calc_wilder_smooth(dx_list, period)
    # normalize: wilder smooth gives cumulative sum, need /period for first value
    # re-normalize from first valid point
    first_valid = next((i for i, v in enumerate(adx) if v == v), None)
    if first_valid is not None:
        adx[first_valid] = adx[first_valid] / period
        for i in range(first_valid + 1, n):
            if adx[i] == adx[i]:
                adx[i] = (adx[i - 1] * (period - 1) + dx_list[i]) / period if dx_list[i] == dx_list[i] else adx[i - 1]

    return {"adx": adx, "plus_di": plus_di, "minus_di": minus_di}


def find_swing_low(bars: list[dict], lookback: int = 5) -> float:
    """최근 lookback 봉 중 최저가 (스톱 기준)."""
    recent = bars[-lookback:]
    return min(b["low"] for b in recent)


# ---------------------------------------------------------------------------
# Setup detection
# ---------------------------------------------------------------------------

def detect_setup(bars: list[dict]) -> dict:
    if len(bars) < 60:
        return {"error": "데이터 부족 (최소 60봉 필요)"}

    closes = [b["close"] for b in bars]
    ema20 = calc_ema(closes, 20)
    adx_data = calc_adx(bars)
    adx = adx_data["adx"]
    plus_di = adx_data["plus_di"]

    last = bars[-1]
    prev = bars[-2]

    cur_adx = adx[-1]
    cur_ema = ema20[-1]
    cur_close = last["close"]
    cur_plus_di = plus_di[-1]

    if cur_adx != cur_adx:  # nan
        return {"error": "ADX 계산 불충분"}

    # ADX 상승 여부: 최근 ADX_RISE_LOOKBACK 봉 평균 기울기
    recent_adx = [v for v in adx[-(ADX_RISE_LOOKBACK + 1):] if v == v]
    adx_rising = len(recent_adx) >= 2 and recent_adx[-1] > recent_adx[0]

    # 20EMA 터치 여부
    ema_distance_pct = (cur_close - cur_ema) / cur_ema
    touching_ema = abs(ema_distance_pct) <= EMA_TOUCH_THRESHOLD

    # 상승 추세 확인 (+DI > 0, price above EMA recently)
    uptrend = cur_plus_di > plus_di_threshold(adx_data, bars)

    # 셋업 성립 조건
    adx_condition = cur_adx > 30 and adx_rising
    setup_active = adx_condition and touching_ema and cur_close > cur_ema * (1 - EMA_TOUCH_THRESHOLD)

    # 진입가 (buy stop): 현재 바 고가 위
    entry_price = last["high"] * 1.001  # 0.1% 슬리피지 여유

    # 스톱로스: 최근 스윙 저점
    stop_loss = find_swing_low(bars)
    risk_pct = (entry_price - stop_loss) / entry_price * 100

    return {
        "symbol": "",
        "close": cur_close,
        "ema20": cur_ema,
        "ema_distance_pct": ema_distance_pct * 100,
        "adx": cur_adx,
        "adx_rising": adx_rising,
        "plus_di": cur_plus_di,
        "adx_condition": adx_condition,
        "touching_ema": touching_ema,
        "setup_active": setup_active,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "risk_pct": risk_pct,
        "prev_high": prev["high"],
    }


def plus_di_threshold(adx_data: dict, bars: list[dict]) -> float:
    """간단 상승 추세 기준: +DI 최근 평균의 80%."""
    recent = [v for v in adx_data["plus_di"][-10:] if v == v]
    if not recent:
        return 0.0
    return sum(recent) / len(recent) * 0.8


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

BOLD = "\033[1m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
RESET = "\033[0m"

FIRE = "[SETUP]"
CHECK = "[OK]   "
WARN = "[WARN] "
INFO = "[INFO] "


def fmt_pct(v: float, decimals: int = 2) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.{decimals}f}%"


def print_result(label: str, r: dict) -> None:
    if "error" in r:
        print(f"  {WARN} {label}: {r['error']}")
        return

    status_icon = f"{GREEN}{FIRE}{RESET}" if r["setup_active"] else f"{CYAN}{CHECK}{RESET}"
    adx_color = GREEN if r["adx_condition"] else YELLOW
    ema_color = GREEN if r["touching_ema"] else RESET
    arrow = "↑" if r["adx_rising"] else "↓"

    print(f"\n  {status_icon} {BOLD}{label}{RESET}")
    print(f"    종가   : {r['close']:.4f}")
    print(f"    20EMA  : {r['ema20']:.4f}  (거리 {ema_color}{fmt_pct(r['ema_distance_pct'])}{RESET})")
    print(f"    ADX(14): {adx_color}{r['adx']:.1f} {arrow}{RESET}  +DI={r['plus_di']:.1f}")

    if r["setup_active"]:
        print(f"    {BOLD}{GREEN}★ 셋업 성립 ★{RESET}")
        print(f"    진입가 (buy stop): {r['entry_price']:.4f}  (전봉 고가 {r['prev_high']:.4f} 돌파)")
        print(f"    스톱로스         : {r['stop_loss']:.4f}  (리스크 {r['risk_pct']:.1f}%)")
    else:
        reasons = []
        if not r["adx_condition"]:
            reasons.append(f"ADX {r['adx']:.1f} {'상승중' if r['adx_rising'] else '하락중'} (조건: >30 & 상승)")
        if not r["touching_ema"]:
            reasons.append(f"EMA 거리 {fmt_pct(r['ema_distance_pct'])} (조건: ±{EMA_TOUCH_THRESHOLD*100:.1f}% 이내)")
        print(f"    미충족: {', '.join(reasons)}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_scan(args: argparse.Namespace) -> None:
    symbols = DEFAULT_SYMBOLS
    if args.symbols:
        symbols = [{"symbol": s, "label": s} for s in args.symbols.split(",")]

    print(f"\n{BOLD}Holy Grail Scanner — ADX(14)>30↑ + 20EMA 풀백{RESET}")
    print(f"{'─' * 55}")

    active_count = 0
    for entry in symbols:
        sym = entry["symbol"]
        label = entry["label"]
        try:
            bars = get_ohlcv(sym, period="6mo", interval="1d")
            r = detect_setup(bars)
            r["symbol"] = sym
            print_result(label, r)
            if r.get("setup_active"):
                active_count += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"  {WARN} {label}: 오류 — {e}")

    print(f"\n{'─' * 55}")
    summary_color = GREEN if active_count > 0 else RESET
    print(f"  스캔 완료: {summary_color}{active_count}개 셋업 감지{RESET} / {len(symbols)}개 종목")


def cmd_check(args: argparse.Namespace) -> None:
    sym = args.symbol
    print(f"\n{BOLD}Holy Grail 상세 분석 — {sym}{RESET}")
    print(f"{'─' * 55}")
    try:
        bars = get_ohlcv(sym, period="6mo", interval="1d")
        r = detect_setup(bars)
        r["symbol"] = sym

        print(f"\n  {INFO} 최근 봉 수: {len(bars)}")
        print_result(sym, r)

        if not r.get("error"):
            print(f"\n  {INFO} 전략 요약 (Street Smarts Ch.10)")
            print(f"    ADX(14) > 30 이면서 상승 + 20EMA 풀백 → buy stop 진입")
            print(f"    Stop: 스윙 저점 | ADX 정점 후 재상승까지 대기")
    except Exception as e:
        print(f"  {WARN} 오류: {e}")


def cmd_watch(args: argparse.Namespace) -> None:
    interval_sec = args.interval
    symbols = DEFAULT_SYMBOLS
    if args.symbols:
        symbols = [{"symbol": s, "label": s} for s in args.symbols.split(",")]

    print(f"{BOLD}Holy Grail 감시 모드{RESET} — {interval_sec}초마다 갱신 (Ctrl+C로 종료)")
    while True:
        print(f"\n[{time.strftime('%H:%M:%S')}]")
        for entry in symbols:
            sym = entry["symbol"]
            label = entry["label"]
            try:
                bars = get_ohlcv(sym, period="3mo", interval="1d")
                r = detect_setup(bars)
                if r.get("setup_active"):
                    print(f"  {GREEN}{FIRE} {label}: 셋업 성립! 진입가={r['entry_price']:.4f}  SL={r['stop_loss']:.4f}{RESET}")
                else:
                    adx_str = f"ADX={r.get('adx', 'N/A'):.1f}" if "adx" in r else "ADX=N/A"
                    print(f"  {CYAN}{CHECK}{RESET} {label}: {adx_str}  EMA거리={fmt_pct(r.get('ema_distance_pct', 0))}")
                time.sleep(0.3)
            except Exception as e:
                print(f"  {WARN} {label}: {e}")
        time.sleep(interval_sec)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Holy Grail Scanner (Street Smarts Ch.10)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python3 scanner.py scan
  python3 scanner.py scan --symbols QQQ,TQQQ,SOXL
  python3 scanner.py check --symbol SPY
  python3 scanner.py watch --interval 300
        """,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="기본 심볼 전체 스캔")
    p_scan.add_argument("--symbols", help="쉼표 구분 심볼 목록 (기본: QQQ,SPY,TIGER,KODEX200)")

    p_check = sub.add_parser("check", help="특정 심볼 상세 분석")
    p_check.add_argument("--symbol", required=True, help="심볼 (예: SPY, 069500.KS)")

    p_watch = sub.add_parser("watch", help="반복 감시 모드")
    p_watch.add_argument("--interval", type=int, default=300, help="갱신 주기(초), 기본 300")
    p_watch.add_argument("--symbols", help="쉼표 구분 심볼 목록")

    args = parser.parse_args()

    if args.cmd == "scan":
        cmd_scan(args)
    elif args.cmd == "check":
        cmd_check(args)
    elif args.cmd == "watch":
        cmd_watch(args)


if __name__ == "__main__":
    main()
