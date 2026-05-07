#!/usr/bin/env python3
import argparse, json, sys, time, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))
from core.regime_classifier import classify as classify_regime

API_BINANCE_F = 'https://fapi.binance.com/fapi/v1/ticker/24hr'
API_BINANCE_S = 'https://api.binance.com/api/v3/ticker/24hr'
API_UPBIT     = 'https://api.upbit.com/v1/ticker'
KLINES_F      = 'https://fapi.binance.com/fapi/v1/klines'
KLINES_S      = 'https://api.binance.com/api/v3/klines'

def fetch_json(url, params=None):
    if params:
        url += '?' + '&'.join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "openclaw-skill"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)

def calc_rsi(closes, period=14):
    """RSI(14). Returns 50.0 when data is insufficient."""
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
    gains  = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_g, avg_l = sum(gains) / period, sum(losses) / period
    return 100.0 if avg_l == 0 else round(100 - 100 / (1 + avg_g / avg_l), 1)

def get_signals_v3(symbol, klines_api):
    """
    Fetches 1h (21 candles) and 15m (5 candles).
    Returns (change_1h, change_15m, rsi14, vol_ratio, bullish_candle_ratio).
    """
    k1h = fetch_json(klines_api, {'symbol': symbol, 'interval': '1h', 'limit': 21})
    closes_1h = [float(k[4]) for k in k1h]
    vols_1h   = [float(k[7]) for k in k1h]

    rsi      = calc_rsi(closes_1h[-15:])
    ch_1h    = round((closes_1h[-1] - closes_1h[-2]) / closes_1h[-2] * 100, 2)

    # volume surge: current vs 20-period average
    avg_vol   = sum(vols_1h[:-1]) / max(len(vols_1h) - 1, 1)
    vol_ratio = round(vols_1h[-1] / avg_vol, 2) if avg_vol > 0 else 1.0

    # trend confirmation: bullish ratio of last 3 candles (close > open)
    recent_k = k1h[-3:]
    bullish  = sum(1 for k in recent_k if float(k[4]) > float(k[1])) / 3

    # 15m momentum over last 4 candles (= 1 hour)
    k15m     = fetch_json(klines_api, {'symbol': symbol, 'interval': '15m', 'limit': 5})
    closes_15m = [float(k[4]) for k in k15m]
    ch_15m   = round((closes_15m[-1] - closes_15m[0]) / closes_15m[0] * 100, 2) if closes_15m[0] != 0 else 0.0

    return ch_1h, ch_15m, rsi, vol_ratio, bullish

def score_v3(ch_1h, ch_15m, vol_usdt, rsi, vol_ratio, bullish,
             rsi_lo=45, rsi_hi=68, min_vol_ratio=1.5, min_bullish=0.67):
    """
    v3 scalping score. Default params for Binance; Upbit uses relaxed thresholds.
    Hard filters (all must pass):
      RSI in [rsi_lo, rsi_hi] | vol_ratio ≥ min_vol_ratio
      ch_15m > 0              | bullish ≥ min_bullish
    """
    if not (rsi_lo <= rsi <= rsi_hi):
        return None
    if vol_ratio < min_vol_ratio:
        return None
    if ch_15m <= 0:
        return None
    if bullish < min_bullish:
        return None

    momentum    = ch_1h * 0.45 + ch_15m * 0.20
    surge_bonus = min(vol_ratio - 1.0, 3.0) * 5      # 0 – 15 pts
    liquidity   = min(vol_usdt, 1e9) / 1e9 * 15
    rsi_room    = (68 - rsi) * 0.15                   # 0 – 3.45 pts

    return round(momentum + surge_bonus + liquidity + rsi_room, 3)

def recommend_binance_futures(regime_params=None):
    rp   = regime_params or {}
    data = fetch_json(API_BINANCE_F)
    candidates = [
        d for d in data
        if float(d.get('priceChangePercent') or 0) > 3
        and float(d.get('quoteVolume') or 0) > 5e7
    ]
    candidates.sort(key=lambda x: float(x.get('quoteVolume', 0)), reverse=True)

    scored = []
    for d in candidates[:40]:
        try:
            ch_1h, ch_15m, rsi, vol_ratio, bullish = get_signals_v3(d['symbol'], KLINES_F)
        except Exception:
            continue
        vol = float(d.get('quoteVolume') or 0)
        s   = score_v3(ch_1h, ch_15m, vol, rsi, vol_ratio, bullish,
                       rsi_lo=rp.get('rsi_lo', 45),
                       rsi_hi=rp.get('rsi_hi', 68),
                       min_vol_ratio=rp.get('vol_ratio_min', 1.5))
        if s is None:
            continue
        scored.append((s, d, ch_1h, ch_15m, rsi, vol_ratio))
        time.sleep(0.08)

    top = []
    for s, d, ch_1h, ch_15m, rsi, vol_ratio in sorted(scored, key=lambda x: x[0], reverse=True)[:3]:
        top.append({
            'symbol':      d['symbol'],
            'score':       s,
            'lastPrice':   d.get('lastPrice'),
            'change_1h':   ch_1h,
            'change_15m':  ch_15m,
            'rsi_14':      rsi,
            'vol_ratio':   vol_ratio,
            'quoteVolume': d.get('quoteVolume'),
        })
    return top

def recommend_binance_spot(regime_params=None):
    rp   = regime_params or {}
    data = fetch_json(API_BINANCE_S)
    candidates = [
        d for d in data
        if float(d.get('priceChangePercent') or 0) > 3
        and float(d.get('quoteVolume') or 0) > 2e7
    ]
    candidates.sort(key=lambda x: float(x.get('quoteVolume', 0)), reverse=True)

    scored = []
    for d in candidates[:30]:
        try:
            ch_1h, ch_15m, rsi, vol_ratio, bullish = get_signals_v3(d['symbol'], KLINES_S)
        except Exception:
            continue
        vol = float(d.get('quoteVolume') or 0)
        s   = score_v3(ch_1h, ch_15m, vol, rsi, vol_ratio, bullish,
                       rsi_lo=rp.get('rsi_lo', 45),
                       rsi_hi=rp.get('rsi_hi', 68),
                       min_vol_ratio=rp.get('vol_ratio_min', 1.5))
        if s is None:
            continue
        scored.append((s, d, ch_1h, ch_15m, rsi, vol_ratio))
        time.sleep(0.08)

    top = []
    for s, d, ch_1h, ch_15m, rsi, vol_ratio in sorted(scored, key=lambda x: x[0], reverse=True)[:3]:
        top.append({
            'symbol':     d['symbol'],
            'score':      s,
            'change_1h':  ch_1h,
            'change_15m': ch_15m,
            'rsi_14':     rsi,
            'vol_ratio':  vol_ratio,
            'volume':     d.get('volume'),
        })
    return top

def recommend_upbit_spot(regime_params=None):
    markets = fetch_json('https://api.upbit.com/v1/market/all')
    krw     = [m['market'] for m in markets if m['market'].startswith('KRW-')]
    tickers = []
    for i in range(0, len(krw), 100):
        batch = krw[i:i + 100]
        tickers += fetch_json(API_UPBIT, {'markets': ','.join(batch)})
        time.sleep(0.12)

    candidates = [t for t in tickers if float(t.get('signed_change_rate', 0)) * 100 > 1]
    candidates.sort(key=lambda x: float(x.get('signed_change_rate', 0)), reverse=True)

    scored, fallback = [], []
    for t in candidates[:25]:
        market = t['market']
        try:
            candles_1h = fetch_json(
                f'https://api.upbit.com/v1/candles/minutes/60?market={market}&count=21'
            )
            closes_1h = [float(c['trade_price']) for c in reversed(candles_1h)]
            vols_1h   = [float(c['candle_acc_trade_price']) for c in reversed(candles_1h)]
            rsi       = calc_rsi(closes_1h[-15:])
            ch_1h     = round((closes_1h[-1] - closes_1h[-2]) / closes_1h[-2] * 100, 2)
            avg_vol   = sum(vols_1h[:-1]) / max(len(vols_1h) - 1, 1)
            vol_ratio = round(vols_1h[-1] / avg_vol, 2) if avg_vol > 0 else 1.0
            raw_k     = candles_1h[:3]  # most recent first
            bullish   = sum(1 for c in raw_k if c['trade_price'] > c['opening_price']) / 3

            candles_15m = fetch_json(
                f'https://api.upbit.com/v1/candles/minutes/15?market={market}&count=5'
            )
            c15 = [float(c['trade_price']) for c in reversed(candles_15m)]
            ch_15m = round((c15[-1] - c15[0]) / c15[0] * 100, 2) if c15[0] != 0 else 0.0
        except Exception:
            continue
        krw_vol  = float(t.get('acc_trade_price_24h') or 0)
        usdt_vol = krw_vol / 1380
        rp = regime_params or {}
        s = score_v3(ch_1h, ch_15m, usdt_vol, rsi, vol_ratio, bullish,
                     rsi_lo=max(42, rp.get('rsi_lo', 42)),
                     rsi_hi=min(70, rp.get('rsi_hi', 70)),
                     min_vol_ratio=rp.get('vol_ratio_min', 1.2),
                     min_bullish=0.50)
        row = (ch_1h, ch_15m, rsi, vol_ratio, t, usdt_vol)
        if s is not None:
            scored.append((s, t, ch_1h, ch_15m, rsi, vol_ratio))
        else:
            # 폴백: 필터 미달이지만 모멘텀 상위 후보 보관
            fallback.append((ch_1h * 0.45 + ch_15m * 0.20, t, ch_1h, ch_15m, rsi, vol_ratio))
        time.sleep(0.15)

    source = scored if scored else fallback
    filter_passed = bool(scored)
    top = []
    for s, t, ch_1h, ch_15m, rsi, vol_ratio in sorted(source, key=lambda x: x[0], reverse=True)[:3]:
        top.append({
            'market':         t['market'],
            'score':          round(s, 3),
            'change_1h':      ch_1h,
            'change_15m':     ch_15m,
            'rsi_14':         rsi,
            'vol_ratio':      vol_ratio,
            'acc_volume_24h': t.get('acc_trade_volume_24h'),
            'filter_passed':  filter_passed,
        })
    return top

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--exchange', required=True)
    p.add_argument('--market',   required=True, choices=['futures', 'spot'])
    args = p.parse_args()

    regime, regime_params = classify_regime()

    out = {
        'exchange':      args.exchange,
        'market':        args.market,
        'regime':        regime,
        'regime_params': regime_params,
        'top3':          [],
    }
    if args.exchange.lower() == 'binance' and args.market == 'futures':
        out['top3'] = recommend_binance_futures(regime_params)
    elif args.exchange.lower() == 'binance' and args.market == 'spot':
        out['top3'] = recommend_binance_spot(regime_params)
    elif args.exchange.lower() == 'upbit' and args.market == 'spot':
        out['top3'] = recommend_upbit_spot(regime_params)
    else:
        out['error'] = 'Unsupported exchange/market combo'
    print(json.dumps(out, indent=2, ensure_ascii=False))
