---
name: second-play
description: "Aschenbrenner 2차 병목 투자 철학 기반 종목 스크리너. '트렌드 탐색 → 키워드 입력 → 병목 종목 선정' 3단계 워크플로우를 제공합니다."
version: 2.0.0
tags: [us-stock, ai-infrastructure, screening, investment]
---

# Aschenbrenner 2차 병목 투자 스크리너

> "AI의 진짜 병목은 전기다" — Leopold Aschenbrenner  
> 1차 수혜주(NVDA)가 아닌 **2차 병목**(전력·데이터센터·광통신·소재)에 투자하는 철학을 코드화한 스크리너입니다.

## 투자 철학 (불변 필터)

1. **2차 병목 정렬도** — AI 인프라 확장의 실질 병목에 연결된 종목인가
2. **서사 모멘텀** — 최근 가격 흐름에서 긍정 시그널이 증가하는가
3. **로테이션 신호** — 현재 섹터로 자금이 유입되고 있는가

트렌드가 이 3원칙에 부합하지 않으면 `discover` 모드가 명시적으로 경고합니다.

---

## 3단계 워크플로우

```
trends → discover → screen
```

### Step 1 — `trends`: 지금 어느 병목 세부 테마가 뜨는가

ETF 수익률로 10개 병목 세부 테마의 현재 모멘텀을 탐색합니다.

```bash
python3 skills/aschenbrenner-screen/scripts/screen.py trends
python3 skills/aschenbrenner-screen/scripts/screen.py trends --top 3
```

**출력:** 5일 수익률 기준 상위 테마 + 사용 가능한 키워드 목록

---

### Step 2 — `discover`: 트렌드 키워드 → 병목 종목 선정

관심 트렌드 키워드를 던지면 Aschenbrenner 렌즈로 해당 병목 종목을 스크리닝합니다.

```bash
# 단일 키워드
python3 skills/aschenbrenner-screen/scripts/screen.py discover --trend nuclear
python3 skills/aschenbrenner-screen/scripts/screen.py discover --trend copper
python3 skills/aschenbrenner-screen/scripts/screen.py discover --trend cooling
python3 skills/aschenbrenner-screen/scripts/screen.py discover --trend optical

# 한국어 키워드도 지원
python3 skills/aschenbrenner-screen/scripts/screen.py discover --trend 원자력
python3 skills/aschenbrenner-screen/scripts/screen.py discover --trend 전력망

# Top N 조정
python3 skills/aschenbrenner-screen/scripts/screen.py discover --trend nuclear --top 5
```

**지원 키워드:**
| 분야 | 키워드 |
|------|--------|
| 전력 | nuclear, 원자력, smr, gas, 천연가스, grid, 변압기, transformer, power, 전력 |
| 데이터센터 | datacenter, 데이터센터, cooling, 냉각, reit, construction, 건설 |
| 광통신 | optical, 광통신, transceiver, networking, 네트워킹, switch |
| 소재 | copper, 구리 |
| BTC→AI | bitcoin, btc, mining, 채굴, miner |

> 매핑되지 않는 키워드(예: quantum computing)는 Aschenbrenner 병목 프레임워크 외 트렌드임을 명시합니다.

---

### Step 3 — `screen`: 표준 4대 카테고리 전체 스크리닝

Aschenbrenner 원본 포트폴리오 12종목 기준 스크리닝입니다.

```bash
python3 skills/aschenbrenner-screen/scripts/screen.py screen
python3 skills/aschenbrenner-screen/scripts/screen.py screen --category power
python3 skills/aschenbrenner-screen/scripts/screen.py screen --category btc-ai --top 3
```

---

## 신호 기준

| 신호 | 조건 | 의미 |
|------|------|------|
| BUY | 점수 ≥ 2.0 | 모멘텀·로테이션 모두 강함 |
| WATCH | 0 ≤ 점수 < 2.0 | 중립, 모니터링 |
| EXIT | 점수 < 0 | 모멘텀 약화, 로테이션 이탈 |

## 전체 병목 강도

| 강도 | 기준 | 해석 |
|------|------|------|
| HIGH | BUY 비율 ≥ 60% | 2차 병목 테마 전반적 강세 |
| MED | BUY 비율 30~60% | 일부 테마만 강세 |
| LOW | BUY 비율 < 30% | 병목 테마 전반 약세, 로테이션 점검 |

## 병목 세부 테마 유니버스

| 테마 | ETF 프록시 | 주요 종목 | 위험도 |
|------|-----------|---------|--------|
| 원자력 | NLR | CEG, VST, CCJ, NNE, SMR, BWX | 일반 |
| 천연가스 | FCG | NRG, VST, EQT, AR, CTRA | 일반 |
| 전력망·변압기 | GRID | ETN, HUBB, GE, PWR, EME | 일반 |
| 데이터센터 REIT | VNQ | EQIX, DLR, AMT, IRM, CCI | 일반 |
| 냉각 시스템 | XLI | VLTO, TT, JCI, CARR, GTLS | 일반 |
| 데이터센터 건설 | XLI | PWR, EME, EXPO, MYR | 일반 |
| 광통신 부품 | IGN | COHR, LITE, AAOI, IIVI | 일반 |
| 네트워킹 장비 | IGN | ANET, CSCO, CIEN, JNPR | 일반 |
| 구리 소재 | COPX | FCX, SCCO, HBM, TECK | 일반 |
| BTC채굴→AI전환 | WGMI | CORZ, IREN, CIFR, RIOT, MARA | **높음** |

## Files

- `scripts/screen.py` — 메인 스크립트 (stdlib only, Yahoo Finance chart API)

## Notes & safety

- 모든 데이터는 Yahoo Finance 공개 API 기반 (인증 불필요)
- **투자 자문 아님** — Aschenbrenner 프레임워크 기반 참고용 스크리닝 도구
- BTC-AI 카테고리는 변동성이 매우 크므로 포트폴리오 **5% 이내** 권장
- "큰 서사"(AI 인프라 붐)가 약화 조짐이면 빠른 로테이션 필요
