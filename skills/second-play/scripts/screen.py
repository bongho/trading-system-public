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
import argparse, json, time, urllib.request
from typing import Any

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

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"
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
    """Yahoo Finance chart API로 단일 종목 5일치 OHLCV 조회."""
    try:
        raw = fetch_json(YAHOO_CHART.format(symbol=symbol))
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


def _pct(closes: list[float], i: int, j: int) -> float:
    try:
        base, head = closes[i], closes[j]
        return (head - base) / base * 100 if base else 0.0
    except (IndexError, TypeError):
        return 0.0


def score_stock(data: dict, risk: str) -> float:
    """
    Aschenbrenner 3원칙 점수화:
      서사 모멘텀 (60%) — 1d 주도, 5d 보조
      로테이션 신호 (40%) — 평균 대비 거래량 비율
      변동성 패널티   — risk=high 시 0.5x
    """
    momentum = data.get("change_1d_pct", 0) * 0.7 + data.get("change_5d_pct", 0) * 0.3
    rotation = min((data.get("volume_ratio", 1.0) - 1.0) * 100, 100)
    raw = momentum * 0.6 + rotation * 0.4
    return round(raw * (0.5 if risk == "high" else 1.0), 3)


def to_signal(score: float) -> str:
    return "BUY" if score >= 2.0 else ("WATCH" if score >= 0 else "EXIT")


def fetch_and_score(symbols: list[str], risk: str, top_n: int) -> tuple[list[dict], list[dict]]:
    """종목 리스트 조회·점수화·정렬 후 (top_n, all) 반환."""
    ranked = []
    for sym in symbols:
        data = get_chart(sym)
        if "error" not in data:
            s = score_stock(data, risk)
            ranked.append({**data, "score": s, "signal": to_signal(s)})
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

def cmd_screen(category: str, top_n: int) -> None:
    cats = LEGACY_CATEGORIES if category == "all" else {category: LEGACY_CATEGORIES[category]}
    results: dict[str, Any] = {}
    total_buy, total_top = 0, 0

    for key, cfg in cats.items():
        top, all_stocks = fetch_and_score(cfg["stocks"], cfg["risk"], top_n)
        results[key] = {"label": cfg["label"], "risk": cfg["risk"], "top": top, "all": all_stocks}
        total_buy += sum(1 for s in top if s["signal"] == "BUY")
        total_top += len(top)

    strength = bottleneck_strength(
        [s for cat in results.values() for s in cat["top"]], total_top
    )
    print(json.dumps({
        "mode": "screen",
        "category": category,
        "bottleneck_strength": strength,
        "summary": f"오늘의 AI 2차 병목 강도: {strength} ({total_buy}/{total_top} BUY)",
        "results": results,
    }, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Command: trends — ETF 수익률로 뜨는 병목 세부 테마 탐색
# ---------------------------------------------------------------------------

def cmd_trends(top_n: int) -> None:
    etf_results = []
    seen_etfs: set[str] = set()

    for key, cfg in BOTTLENECK_UNIVERSE.items():
        etf = cfg["etf"]
        if etf in seen_etfs:
            continue
        seen_etfs.add(etf)
        data = get_chart(etf)
        if "error" not in data:
            etf_results.append({
                "theme_key": key,
                "label": cfg["label"],
                "angle": cfg["angle"],
                "etf": etf,
                "change_1d_pct": data["change_1d_pct"],
                "change_5d_pct": data["change_5d_pct"],
                "volume_ratio": data["volume_ratio"],
            })
        time.sleep(0.08)

    etf_results.sort(key=lambda x: x["change_5d_pct"], reverse=True)

    print(json.dumps({
        "mode": "trends",
        "note": "선택 후 → python3 screen.py discover --trend <키워드>",
        "top_themes": etf_results[:top_n],
        "all_themes": etf_results,
        "available_keywords": sorted(KEYWORD_MAP.keys()),
    }, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Command: discover — 트렌드 키워드 → Aschenbrenner 렌즈로 종목 선정
# ---------------------------------------------------------------------------

def cmd_discover(trend: str, top_n: int) -> None:
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
        top, all_stocks = fetch_and_score(cfg["stocks"], cfg["risk"], top_n)
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

    print(json.dumps({
        "mode": "discover",
        "trend": trend,
        "matched": True,
        "matched_themes": matched_themes,
        "bottleneck_strength": strength,
        "summary": (
            f"'{trend}' 트렌드의 2차 병목 강도: {strength} "
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

    # screen
    p_screen = sub.add_parser("screen", help="표준 4대 카테고리 스크리닝")
    p_screen.add_argument(
        "--category", default="all",
        choices=["all", "power", "datacenter", "optical", "btc-ai"],
    )
    p_screen.add_argument("--top", type=int, default=2, help="카테고리별 추천 수 (기본: 2)")

    # trends
    p_trends = sub.add_parser("trends", help="ETF 수익률로 병목 세부 테마 탐색")
    p_trends.add_argument("--top", type=int, default=5, help="상위 N개 테마 (기본: 5)")

    # discover
    p_discover = sub.add_parser("discover", help="트렌드 키워드 → 2차 병목 종목 선정")
    p_discover.add_argument("--trend", required=True, help="예: nuclear, copper, cooling, optical")
    p_discover.add_argument("--top", type=int, default=3, help="테마별 추천 수 (기본: 3)")

    args = p.parse_args()

    if args.cmd == "screen":
        cmd_screen(args.category, args.top)
    elif args.cmd == "trends":
        cmd_trends(args.top)
    elif args.cmd == "discover":
        cmd_discover(args.trend, args.top)


if __name__ == "__main__":
    main()
