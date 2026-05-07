"""
Market regime classifier.

Combines NASDAQ 24h return, BTC 24h return, and BTC perpetual funding rate
to classify current market state into one of three regimes.

Returns one of: 'risk_on' | 'risk_off' | 'neutral'
"""
import json
import urllib.request
from typing import Tuple

REGIME_PARAMS = {
    'risk_on':  {'rsi_lo': 45, 'rsi_hi': 68, 'vol_ratio_min': 1.5},
    'neutral':  {'rsi_lo': 47, 'rsi_hi': 65, 'vol_ratio_min': 1.8},
    'risk_off': {'rsi_lo': 50, 'rsi_hi': 62, 'vol_ratio_min': 2.5},
}


def _fetch(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={'Accept': 'application/json', 'User-Agent': 'openclaw-regime'},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def _qqq_change_pct() -> float:
    """QQQ 24h % change via Yahoo Finance chart API (no library required)."""
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/QQQ?interval=1d&range=2d'
    data = _fetch(url)
    closes = data['chart']['result'][0]['indicators']['quote'][0]['close']
    closes = [c for c in closes if c is not None]
    if len(closes) < 2:
        return 0.0
    return (closes[-1] - closes[-2]) / closes[-2] * 100


def _btc_change_pct() -> float:
    """BTC-KRW 24h % change via Upbit public API."""
    data = _fetch('https://api.upbit.com/v1/ticker?markets=KRW-BTC')
    return float(data[0]['signed_change_rate']) * 100


def _btc_funding_rate() -> float:
    """Latest BTC perpetual funding rate from Binance FAPI."""
    data = _fetch('https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1')
    return float(data[0]['fundingRate'])


def classify() -> Tuple[str, dict]:
    """
    Returns (regime, score_params) where score_params contains keys:
      rsi_lo, rsi_hi, vol_ratio_min
    Falls back to 'neutral' if any data source is unavailable.
    """
    try:
        nasdaq = _qqq_change_pct()
    except Exception:
        nasdaq = 0.0

    try:
        btc = _btc_change_pct()
    except Exception:
        btc = 0.0

    try:
        funding = _btc_funding_rate()
    except Exception:
        funding = 0.0

    # risk_on: NASDAQ rising, BTC rising, funding not overheated
    if nasdaq > 0.5 and btc > 1.0 and funding < 0.05:
        regime = 'risk_on'
    # risk_off: NASDAQ falling hard, BTC selling off, or funding overheated
    elif nasdaq < -1.0 or btc < -2.0 or funding > 0.08:
        regime = 'risk_off'
    else:
        regime = 'neutral'

    return regime, REGIME_PARAMS[regime]


if __name__ == '__main__':
    regime, params = classify()
    print(f"Regime : {regime}")
    print(f"Params : {params}")
