#!/usr/bin/env python3
# Aschenbrenner 2nd-bottleneck investment screener
# Philosophy: AI의 진짜 병목은 전기·인프라다 — 1차 수혜주(NVDA)보다 2차 병목에 투자
#
# Modes:
#   screen   — 표준 4개 카테고리 스크리닝 (기존 동작)
#   trends   — ETF 수익률로 어느 병목 세부 테마가 지금 뜨는지 탐색
#   discover — 트렌드 키워드 입력 → Aschenbrenner 렌즈로 매칭 종목 스크리닝
#
# Data: Yahoo Finance chart API (stdlib only, no auth)

from __future__ import annotations
import argparse, json, sys, time, urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parents[3]))
try:
    from core.enrichment import earnings_surprise_multiplier as _earnings_mult
except ImportError:
    def _earnings_mult(symbol: str) -> float:  # graceful fallback
        return 1.0

# ---------------------------------------------------------------------------
# 2차 병목 세부 유니버스
# 카테고리 → 세부 테마 → 종목
# ---------------------------------------------------------------------------
BOTTLENECK_UNIVERSE: dict[str, dict[str, Any]] = {
    # ── 전력 인프라 ──────────────────────────────────────────────
    "power-nuclear": {
        "parent": "power",
        "label": "원자력 발전",
        "angle": "AI 전력의 안정적 기저부하 — 24/7 무탄소 전력",
        "etf": "NLR",
        "risk": "normal",
        "stocks": ["CEG", "NRG", "VST", "CCJ", "NNE", "SMR", "BWX"],
    },
    "power-gas": {
        "parent": "power",
        "label": "천연가스 발전",
        "angle": "AI 데이터센터 피크 전력 → 가스 피커 수요",
        "etf": "FCG",
        "risk": "normal",
        "stocks": ["NRG", "VST", "EQT", "AR", "CTRA", "CHK"],
    },
    "power-grid": {
        "parent": "power",
        "label": "전력망·변압기·송전",
        "angle": "AI 전력 수요 폭증 → 송전망·변압기 공급 병목",
        "etf": "GRID",
        "risk": "normal",
        "stocks": ["ETN", "HUBB", "GE", "PWR", "EME", "AMSC", "MYR"],
    },
    # ── 데이터센터 ───────────────────────────────────────────────
    "datacenter-reit": {
        "parent": "datacenter",
        "label": "데이터센터 REIT",
        "angle": "AI 클러스터 부지·전력 확보가 병목",
        "etf": "VNQ",
        "risk": "normal",
        "stocks": ["EQIX", "DLR", "AMT", "IRM", "CCI", "CONE"],
    },
    "datacenter-cooling": {
        "parent": "datacenter",
        "label": "냉각 시스템",
        "angle": "AI 칩 발열 → 액냉·공냉 인프라 수요",
        "etf": "XLI",
        "risk": "normal",
        "stocks": ["VLTO", "TT", "JCI", "CARR", "GTLS", "AAON"],
    },
    "datacenter-construction": {
        "parent": "datacenter",
        "label": "데이터센터 건설·전기공사",
        "angle": "AI 인프라 물리적 건설 병목",
        "etf": "XLI",
        "risk": "normal",
        "stocks": ["PWR", "EME", "EXPO", "MYR", "TNET", "GLDD"],
    },
    # ── 광통신·네트워킹 ──────────────────────────────────────────
    "optical-components": {
        "parent": "optical",
        "label": "광통신 부품",
        "angle": "AI 클러스터 간 대역폭 병목 — 광트랜시버",
        "etf": "IGN",
        "risk": "normal",
        "stocks": ["COHR", "LITE", "AAOI", "NPTN", "IIVI", "FNSR"],
    },
    "optical-networking": {
        "parent": "optical",
        "label": "네트워킹 장비",
        "angle": "AI 패브릭 스위치·라우터 병목",
        "etf": "IGN",
        "risk": "normal",
        "stocks": ["ANET", "CSCO", "CIEN", "JNPR", "INFN", "CALX"],
    },
    # ── 소재 ─────────────────────────────────────────────────────
    "material-copper": {
        "parent": "material",
        "label": "구리 (AI 전력 배선)",
        "angle": "AI 인프라 전기 배선 소재 병목 — 구리 수요 폭증",
        "etf": "COPX",
        "risk": "normal",
        "stocks": ["FCX", "SCCO", "HBM", "TECK", "CMMC", "ERO"],
    },
    # ── BTC채굴 → AI전환 ─────────────────────────────────────────
    "btc-ai-mining": {
        "parent": "btc-ai",
        "label": "BTC채굴 → AI 인프라 전환",
        "angle": "채굴 시설의 GPU 클러스터 전환 — 고변동성",
        "etf": "WGMI",
        "risk": "high",
        "stocks": ["CORZ", "IREN", "CIFR", "RIOT", "MARA", "HUT"],
    },
}

# 기존 4대 카테고리 (표준 screen 모드용)
LEGACY_CATEGORIES: dict[str, dict[str, Any]] = {
    "power":      {"label": "전력 인프라",        "risk": "normal", "stocks": ["VST", "CEG", "NRG"]},
    "datacenter": {"label": "데이터센터 REIT",     "risk": "normal", "stocks": ["EQIX", "DLR", "AMT"]},
    "optical":    {"label": "광통신 장비",         "risk": "normal", "stocks": ["COHR", "LITE"]},
    "btc-ai":     {"label": "BTC채굴→AI전환",      "risk": "high",   "stocks": ["CORZ", "IREN", "CIFR"]},
}

# 키워드 → 세부 테마 매핑 (discover 모드)
KEYWORD_MAP: dict[str, list[str]] = {
    # 전력
    "nuclear": ["power-nuclear"],
    "원자력": ["power-nuclear"],
    "smr": ["power-nuclear"],
    "natural gas": ["power-gas"],
    "천연가스": ["power-gas"],
    "gas": ["power-gas"],
    "grid": ["power-grid"],
    "변압기": ["power-grid"],
    "transformer": ["power-grid"],
    "transmission": ["power-grid"],
    "송전": ["power-grid"],
    "power": ["power-nuclear", "power-gas", "power-grid"],
    "전력": ["power-nuclear", "power-gas", "power-grid"],
    "electricity": ["power-nuclear", "power-gas", "power-grid"],
    # 데이터센터
    "cooling": ["datacenter-cooling"],
    "냉각": ["datacenter-cooling"],
    "liquid cooling": ["datacenter-cooling"],
    "datacenter": ["datacenter-reit", "datacenter-cooling", "datacenter-construction"],
    "데이터센터": ["datacenter-reit", "datacenter-cooling", "datacenter-construction"],
    "reit": ["datacenter-reit"],
    "construction": ["datacenter-construction"],
    "건설": ["datacenter-construction"],
    # 광통신
    "optical": ["optical-components", "optical-networking"],
    "광통신": ["optical-components", "optical-networking"],
    "transceiver": ["optical-components"],
    "트랜시버": ["optical-components"],
    "networking": ["optical-networking"],
    "네트워킹": ["optical-networking"],
    "switch": ["optical-networking"],
    "스위치": ["optical-networking"],
    # 소재
    "copper": ["material-copper"],
    "구리": ["material-copper"],
    # BTC·채굴
    "bitcoin": ["btc-ai-mining"],
    "mining": ["btc-ai-mining"],
    "채굴": ["btc-ai-mining"],
    "btc": ["btc-ai-mining"],
    "miner": ["btc-ai-mining"],
}

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range}&interval={interval}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (freedom-skill/1.0)",
    "Accept": "application/json",
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def fetch_json(url: str) -> Any:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def get_chart(symbol: str) -> dict:
    """단기(5d) 데이터 조회."""
    try:
        raw = fetch_json(YAHOO_CHART.format(symbol=symbol, range="5d", interval="1d"))
        result = raw["chart"]["result"][0]
        meta = result.get("meta", {})
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]

        closes = [c for c in (quote.get("close") or []) if c is not None]
        volumes = [v for v in (quote.get("volume") or []) if v is not None]

        avg_vol = (sum(volumes[:-1]) / len(volumes[:-1])) if len(volumes) > 1 else 0
        vol_ratio = round(volumes[-1] / avg_vol, 2) if avg_vol else 1.0

        return {
            "symbol": symbol,
            "name": meta.get("shortName", symbol),
            "price": round(meta.get("regularMarketPrice") or (closes[-1] if closes else 0), 2),
            "change_1d_pct": round(_pct(closes, -2, -1), 2),
            "change_5d_pct": round(_pct(closes, 0, -1), 2),
            "volume_ratio": vol_ratio,
        }
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}


def get_chart_long(symbol: str) -> dict:
    """장기(1y) 데이터 조회 — 1m/3m/6m/1y 수익률 및 추세 일관성 계산."""
    try:
        raw = fetch_json(YAHOO_CHART.format(symbol=symbol, range="1y", interval="1d"))
        result = raw["chart"]["result"][0]
        meta = result.get("meta", {})
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]

        closes = [c for c in (quote.get("close") or []) if c is not None]
        n = len(closes)

        # 기간별 수익률 (거래일 기준: 1m≈21d, 3m≈63d, 6m≈126d)
        change_1m  = round(_pct(closes, max(0, n - 22), -1), 2)
        change_3m  = round(_pct(closes, max(0, n - 64), -1), 2)
        change_6m  = round(_pct(closes, max(0, n - 127), -1), 2)
        change_1y  = round(_pct(closes, 0, -1), 2)

        # 추세 일관성: 1m/3m/6m 중 플러스인 기간 수
        positive_periods = sum(1 for v in [change_1m, change_3m, change_6m] if v > 0)

        return {
            "symbol": symbol,
            "name": meta.get("shortName", symbol),
            "price": round(meta.get("regularMarketPrice") or (closes[-1] if closes else 0), 2),
            "change_1m_pct": change_1m,
            "change_3m_pct": change_3m,
            "change_6m_pct": change_6m,
            "change_1y_pct": change_1y,
            "trend_consistency": positive_periods,  # 0~3 (3=모든 기간 플러스)
        }
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}


def _pct(closes: list[float], i: int, j: int) -> float:
    try:
        base, head = closes[i], closes[j]
        return (head - base) / base * 100 if base else 0.0
    except (IndexError, TypeError):
        return 0.0


def score_stock(data: dict, risk: str) -> float:
    """단기 스코어: 1d 모멘텀(60%) + 거래량 로테이션(40%)."""
    momentum = data.get("change_1d_pct", 0) * 0.7 + data.get("change_5d_pct", 0) * 0.3
    rotation = min((data.get("volume_ratio", 1.0) - 1.0) * 100, 100)
    raw = momentum * 0.6 + rotation * 0.4
    return round(raw * (0.5 if risk == "high" else 1.0), 3)


def score_stock_long(data: dict, risk: str) -> float:
    """
    장기 스코어 (Aschenbrenner 구조적 병목 관점):
      성장 모멘텀 (50%) — 6m(45%) + 3m(35%) + 1m(20%) 가중 평균 (장기일수록 비중↑)
      추세 일관성 (30%) — 1m/3m/6m 중 플러스 기간 비율
      1y 방향성  (20%) — 1년 수익률 (구조적 트렌드 확인)
      변동성 패널티 — risk=high 시 0.5x
    """
    c1m = data.get("change_1m_pct", 0)
    c3m = data.get("change_3m_pct", 0)
    c6m = data.get("change_6m_pct", 0)
    c1y = data.get("change_1y_pct", 0)
    consistency = data.get("trend_consistency", 0)  # 0~3

    momentum = c6m * 0.45 + c3m * 0.35 + c1m * 0.20
    trend = (consistency / 3) * 100  # 0~100 정규화
    direction = min(max(c1y, -50), 100)  # 1y 수익률 cap

    raw = momentum * 0.50 + trend * 0.30 + direction * 0.20
    return round(raw * (0.5 if risk == "high" else 1.0), 3)


def to_signal(score: float, horizon: str = "short") -> str:
    if horizon == "long":
        # 장기: 더 높은 기준 (sustained uptrend 필요)
        return "BUY" if score >= 8.0 else ("WATCH" if score >= 0 else "EXIT")
    return "BUY" if score >= 2.0 else ("WATCH" if score >= 0 else "EXIT")


def fetch_and_score(
    symbols: list[str],
    risk: str,
    top_n: int,
    horizon: str = "short",
    enrich: bool = False,
) -> tuple[list[dict], list[dict]]:
    """종목 리스트 조회·점수화·정렬 후 (top_n, all) 반환.

    enrich=True 시 Yahoo Finance 실적 서프라이즈 배수를 score에 반영.
    """
    ranked = []
    for sym in symbols:
        data = get_chart_long(sym) if horizon == "long" else get_chart(sym)
        if "error" not in data:
            s = score_stock_long(data, risk) if horizon == "long" else score_stock(data, risk)
            if enrich:
                mult = _earnings_mult(sym)
                s    = round(s * mult, 3)
                data['earnings_multiplier'] = mult
            ranked.append({**data, "score": s, "signal": to_signal(s, horizon), "horizon": horizon})
        time.sleep(0.08)
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked[:top_n], ranked


def bottleneck_strength(top: list[dict], top_n: int) -> str:
    buy = sum(1 for r in top if r["signal"] == "BUY")
    ratio = buy / top_n if top_n else 0
    return "HIGH" if ratio >= 0.6 else ("MED" if ratio >= 0.3 else "LOW")


# ---------------------------------------------------------------------------
# Command: screen — 표준 4대 카테고리 스크리닝
# ---------------------------------------------------------------------------

def cmd_screen(category: str, top_n: int, horizon: str = "short", enrich: bool = False) -> None:
    cats = LEGACY_CATEGORIES if category == "all" else {category: LEGACY_CATEGORIES[category]}
    results: dict[str, Any] = {}
    total_buy, total_top = 0, 0

    for key, cfg in cats.items():
        top, all_stocks = fetch_and_score(cfg["stocks"], cfg["risk"], top_n, horizon, enrich=enrich)
        results[key] = {"label": cfg["label"], "risk": cfg["risk"], "top": top, "all": all_stocks}
        total_buy += sum(1 for s in top if s["signal"] == "BUY")
        total_top += len(top)

    strength = bottleneck_strength(
        [s for cat in results.values() for s in cat["top"]], total_top
    )
    horizon_label = "장기(1m/3m/6m/1y)" if horizon == "long" else "단기(1d/5d)"
    print(json.dumps({
        "mode": "screen",
        "horizon": horizon_label,
        "category": category,
        "bottleneck_strength": strength,
        "summary": f"[{horizon_label}] AI 2차 병목 강도: {strength} ({total_buy}/{total_top} BUY)",
        "results": results,
    }, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Command: trends — ETF 수익률로 뜨는 병목 세부 테마 탐색
# ---------------------------------------------------------------------------

def cmd_trends(top_n: int, horizon: str = "short") -> None:
    etf_results = []
    seen_etfs: set[str] = set()

    for key, cfg in BOTTLENECK_UNIVERSE.items():
        etf = cfg["etf"]
        if etf in seen_etfs:
            continue
        seen_etfs.add(etf)
        data = get_chart_long(etf) if horizon == "long" else get_chart(etf)
        if "error" not in data:
            if horizon == "long":
                entry = {
                    "theme_key": key, "label": cfg["label"], "angle": cfg["angle"], "etf": etf,
                    "change_1m_pct": data["change_1m_pct"],
                    "change_3m_pct": data["change_3m_pct"],
                    "change_6m_pct": data["change_6m_pct"],
                    "change_1y_pct": data["change_1y_pct"],
                    "trend_consistency": data["trend_consistency"],
                }
                sort_key = data["change_6m_pct"]
            else:
                entry = {
                    "theme_key": key, "label": cfg["label"], "angle": cfg["angle"], "etf": etf,
                    "change_1d_pct": data["change_1d_pct"],
                    "change_5d_pct": data["change_5d_pct"],
                    "volume_ratio": data["volume_ratio"],
                }
                sort_key = data["change_5d_pct"]
            etf_results.append({**entry, "_sort": sort_key})
        time.sleep(0.08)

    etf_results.sort(key=lambda x: x.pop("_sort"), reverse=True)
    horizon_label = "장기(6m 기준)" if horizon == "long" else "단기(5d 기준)"

    print(json.dumps({
        "mode": "trends",
        "horizon": horizon_label,
        "note": "선택 후 → python3 screen.py discover --trend <키워드> --horizon " + horizon,
        "top_themes": etf_results[:top_n],
        "all_themes": etf_results,
        "available_keywords": sorted(KEYWORD_MAP.keys()),
    }, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Command: discover — 트렌드 키워드 → Aschenbrenner 렌즈로 종목 선정
# ---------------------------------------------------------------------------

def cmd_discover(trend: str, top_n: int, horizon: str = "short", enrich: bool = False) -> None:
    trend_lower = trend.lower().strip()

    # 키워드 매핑 (부분 매치 포함)
    matched_themes: list[str] = []
    for kw, themes in KEYWORD_MAP.items():
        if kw in trend_lower or trend_lower in kw:
            for t in themes:
                if t not in matched_themes:
                    matched_themes.append(t)

    if not matched_themes:
        # 매핑 없음 — Aschenbrenner 프레임워크 외 트렌드일 수 있음
        print(json.dumps({
            "mode": "discover",
            "trend": trend,
            "matched": False,
            "message": (
                f"'{trend}'은 Aschenbrenner 2차 병목 프레임워크와 직접 매핑되지 않습니다. "
                "이 트렌드가 AI 인프라 확장의 병목(전력·데이터센터·광통신·소재)에 "
                "연결되는지 검토하세요."
            ),
            "available_keywords": sorted(KEYWORD_MAP.keys()),
        }, ensure_ascii=False, indent=2))
        return

    results: dict[str, Any] = {}
    all_signals: list[dict] = []

    for theme_key in matched_themes:
        cfg = BOTTLENECK_UNIVERSE[theme_key]
        top, all_stocks = fetch_and_score(cfg["stocks"], cfg["risk"], top_n, horizon, enrich=enrich)
        results[theme_key] = {
            "label": cfg["label"],
            "angle": cfg["angle"],
            "risk": cfg["risk"],
            "top": top,
            "all": all_stocks,
        }
        all_signals.extend(top)

    strength = bottleneck_strength(all_signals, len(all_signals))
    buy_count = sum(1 for s in all_signals if s["signal"] == "BUY")
    horizon_label = "장기(1m/3m/6m/1y)" if horizon == "long" else "단기(1d/5d)"

    print(json.dumps({
        "mode": "discover",
        "horizon": horizon_label,
        "trend": trend,
        "matched": True,
        "matched_themes": matched_themes,
        "bottleneck_strength": strength,
        "summary": (
            f"[{horizon_label}] '{trend}' 트렌드의 2차 병목 강도: {strength} "
            f"({buy_count}/{len(all_signals)} BUY)"
        ),
        "results": results,
    }, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(
        description="Aschenbrenner 2차 병목 투자 스크리너"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    HORIZON_HELP = "투자 성향: short=단기(1d/5d) | long=장기(1m/3m/6m/1y) (기본: short)"

    # screen
    p_screen = sub.add_parser("screen", help="표준 4대 카테고리 스크리닝")
    p_screen.add_argument(
        "--category", default="all",
        choices=["all", "power", "datacenter", "optical", "btc-ai"],
    )
    p_screen.add_argument("--top", type=int, default=2, help="카테고리별 추천 수 (기본: 2)")
    p_screen.add_argument("--horizon", default="short", choices=["short", "long"], help=HORIZON_HELP)

    # trends
    p_trends = sub.add_parser("trends", help="ETF 수익률로 병목 세부 테마 탐색")
    p_trends.add_argument("--top", type=int, default=5, help="상위 N개 테마 (기본: 5)")
    p_trends.add_argument("--horizon", default="short", choices=["short", "long"], help=HORIZON_HELP)

    # discover
    p_discover = sub.add_parser("discover", help="트렌드 키워드 → 2차 병목 종목 선정")
    p_discover.add_argument("--trend", required=True, help="예: nuclear, copper, cooling, optical")
    p_discover.add_argument("--top", type=int, default=3, help="테마별 추천 수 (기본: 3)")
    p_discover.add_argument("--horizon", default="short", choices=["short", "long"], help=HORIZON_HELP)
    p_discover.add_argument("--enrich", action="store_true",
                            help="Yahoo Finance 실적 서프라이즈 배수를 score에 반영")

    # screen --enrich 플래그
    p_screen.add_argument("--enrich", action="store_true",
                          help="Yahoo Finance 실적 서프라이즈 배수를 score에 반영")

    args = p.parse_args()

    if args.cmd == "screen":
        cmd_screen(args.category, args.top, args.horizon, enrich=getattr(args, 'enrich', False))
    elif args.cmd == "trends":
        cmd_trends(args.top, args.horizon)
    elif args.cmd == "discover":
        cmd_discover(args.trend, args.top, args.horizon, enrich=getattr(args, 'enrich', False))


if __name__ == "__main__":
    main()
