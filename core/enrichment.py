"""
Data enrichment helpers (stdlib-only).

Currently provides US stock earnings surprise lookup via Yahoo Finance.
All functions return safe fallback values on any network/parse error.
"""
import json
import urllib.request
from typing import Optional

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (trading-skill/1.0)',
    'Accept': 'application/json',
}
_EARNINGS_URL = (
    'https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}'
    '?modules=earningsHistory'
)


def _fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def earnings_surprise_multiplier(symbol: str) -> float:
    """Score multiplier based on the most recent reported quarterly EPS surprise.

    Returns:
        1.3  — beat estimate by > 2%  (recent positive catalyst)
        1.0  — neutral / data unavailable
        0.85 — missed estimate by > 5%
    """
    try:
        data    = _fetch(_EARNINGS_URL.format(symbol=symbol))
        history = (
            data['quoteSummary']['result'][0]
                 ['earningsHistory']['history']
        )
        if not history:
            return 1.0
        # history is oldest-first; take the last (most recent) quarter
        recent   = history[-1]
        actual   = (recent.get('epsActual')   or {}).get('raw')
        estimate = (recent.get('epsEstimate') or {}).get('raw')
        if actual is None or estimate is None or estimate == 0:
            return 1.0
        surprise = (actual - estimate) / abs(estimate)
        if surprise > 0.02:
            return 1.3
        if surprise < -0.05:
            return 0.85
        return 1.0
    except Exception:
        return 1.0
