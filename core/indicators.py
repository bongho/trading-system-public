"""
stdlib-only 기술 지표 라이브러리.

스킬 코드와 LLM 생성 전략에서 공통으로 임포트한다.
numpy/pandas 불필요 — Docker 이미지 용량 절감.
"""
from __future__ import annotations

import math
from typing import List


def calc_ema(closes: List[float], period: int) -> List[float]:
    """지수이동평균. nan으로 패딩된 리스트 반환 (입력과 같은 길이)."""
    k = 2.0 / (period + 1)
    result: List[float] = []
    for i, v in enumerate(closes):
        if i < period - 1:
            result.append(float("nan"))
        elif i == period - 1:
            result.append(sum(closes[:period]) / period)
        else:
            result.append(v * k + result[-1] * (1 - k))
    return result


def calc_sma(closes: List[float], period: int) -> List[float]:
    result: List[float] = []
    for i in range(len(closes)):
        if i < period - 1:
            result.append(float("nan"))
        else:
            result.append(sum(closes[i - period + 1:i + 1]) / period)
    return result


def calc_rsi(closes: List[float], period: int = 14) -> float:
    """최신 RSI 값 하나만 반환. 데이터 부족 시 50.0."""
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
    gains = [d if d > 0 else 0.0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0.0 for d in deltas[-period:]]
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_g / avg_l), 2)


def calc_atr(bars: List[dict], period: int = 14) -> float:
    """Average True Range (최신 1값). bars = [{high, low, close}, ...]."""
    if len(bars) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return round(atr, 6)


def calc_macd(
    closes: List[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[float, float, float]:
    """MACD 최신 값 (macd_line, signal_line, histogram)."""
    if len(closes) < slow + signal:
        return 0.0, 0.0, 0.0
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    macd_line = [
        (f - s) if (f == f and s == s) else float("nan")
        for f, s in zip(ema_fast, ema_slow)
    ]
    valid = [v for v in macd_line if v == v]
    if len(valid) < signal:
        return 0.0, 0.0, 0.0
    sig_line = calc_ema(valid, signal)
    m = valid[-1]
    s = sig_line[-1] if sig_line[-1] == sig_line[-1] else 0.0
    return round(m, 6), round(s, 6), round(m - s, 6)


def calc_bb(
    closes: List[float],
    period: int = 20,
    std_dev: float = 2.0,
) -> tuple[float, float, float]:
    """볼린저 밴드 최신 값 (upper, mid, lower)."""
    if len(closes) < period:
        c = closes[-1] if closes else 0.0
        return c, c, c
    recent = closes[-period:]
    mid = sum(recent) / period
    variance = sum((x - mid) ** 2 for x in recent) / period
    sd = math.sqrt(variance)
    return round(mid + std_dev * sd, 6), round(mid, 6), round(mid - std_dev * sd, 6)


def calc_adx(bars: List[dict], period: int = 14) -> dict:
    """ADX + +DI + -DI 최신 값. Returns {adx, plus_di, minus_di, adx_rising}."""
    if len(bars) < period * 2 + 5:
        return {"adx": 0.0, "plus_di": 0.0, "minus_di": 0.0, "adx_rising": False}

    tr_list, plus_dm, minus_dm = [], [], []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        tr_list.append(max(h - l, abs(h - pc), abs(l - pc)))
        up = bars[i]["high"] - bars[i - 1]["high"]
        dn = bars[i - 1]["low"] - bars[i]["low"]
        plus_dm.append(up if up > dn and up > 0 else 0.0)
        minus_dm.append(dn if dn > up and dn > 0 else 0.0)

    def _wilder(vals: List[float]) -> List[float]:
        out = [sum(vals[:period])]
        for v in vals[period:]:
            out.append(out[-1] - out[-1] / period + v)
        return out

    atr_s = _wilder(tr_list)
    plus_s = _wilder(plus_dm)
    minus_s = _wilder(minus_dm)

    dx_list = []
    for a, p, m in zip(atr_s, plus_s, minus_s):
        if a == 0:
            dx_list.append(0.0)
            continue
        pdi = 100.0 * p / a
        mdi = 100.0 * m / a
        denom = pdi + mdi
        dx_list.append(100.0 * abs(pdi - mdi) / denom if denom else 0.0)

    adx_list = [sum(dx_list[:period]) / period]
    for dx in dx_list[period:]:
        adx_list.append((adx_list[-1] * (period - 1) + dx) / period)

    cur_adx = adx_list[-1]
    adx_rising = len(adx_list) >= 3 and adx_list[-1] > adx_list[-3]

    last_atr = atr_s[-1]
    plus_di = round(100.0 * plus_s[-1] / last_atr, 2) if last_atr else 0.0
    minus_di = round(100.0 * minus_s[-1] / last_atr, 2) if last_atr else 0.0

    return {
        "adx": round(cur_adx, 2),
        "plus_di": plus_di,
        "minus_di": minus_di,
        "adx_rising": adx_rising,
    }


def find_swing_low(bars: List[dict], lookback: int = 5) -> float:
    return min(b["low"] for b in bars[-lookback:])


def find_swing_high(bars: List[dict], lookback: int = 5) -> float:
    return max(b["high"] for b in bars[-lookback:])
