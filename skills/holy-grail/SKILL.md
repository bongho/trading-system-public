---
name: holy-grail
description: "Street Smarts (Raschke & Connors Ch.10) Holy Grail 전략 스캐너. ADX(14)>30 상승 + 20EMA 풀백 셋업 감지 및 진입가/스톱 자동 산출."
version: 1.0.0
tags: [trading, strategy, adx, ema, trend-following, street-smarts]
---

# Holy Grail Scanner

> ADX(14) > 30 이면서 **상승 중** + 가격이 **20EMA로 풀백** → buy stop 진입  
> 출처: *Street Smarts* — Linda Bradford Raschke & Laurence A. Connors, Chapter 10

---

## 전략 규칙

| 항목 | 조건 |
|------|------|
| **ADX 조건** | 14기간 ADX > 30 이면서 상승 중 (정점 후 재하락 시 대기) |
| **진입 조건** | 가격이 20기간 EMA 접촉 (±1.5% 이내) |
| **진입** | 직전 바 고가 위에 buy stop |
| **Stop** | 최근 스윙 저점 (최근 5봉 최저가) |
| **오버트레이딩 방지** | ADX 정점 이후 재상승까지 새 신호 대기 |

---

## 3가지 모드

```
scan → check → watch
```

### `scan` — 기본 심볼 전체 스캔

셋업 성립 여부를 일괄 확인합니다.

```bash
python3 skills/holy-grail/scripts/scanner.py scan

# 심볼 직접 지정
python3 skills/holy-grail/scripts/scanner.py scan --symbols QQQ,TQQQ,SOXL
```

**기본 감시 대상**: QQQ, SPY, TIGER 미국S&P500 (105930.KS), KODEX 200 (069500.KS)

---

### `check` — 특정 심볼 상세 분석

```bash
python3 skills/holy-grail/scripts/scanner.py check --symbol SPY
python3 skills/holy-grail/scripts/scanner.py check --symbol 069500.KS
```

**출력:**
- 현재 종가 / 20EMA / 거리(%)
- ADX(14) 값 + 상승/하락 방향
- 셋업 성립 여부
- 진입가 (buy stop) + 스톱로스 + 리스크(%)

---

### `watch` — 반복 감시 (장중 알람용)

```bash
# 5분마다 자동 스캔 (기본)
python3 skills/holy-grail/scripts/scanner.py watch

# 10분 간격, 심볼 직접 지정
python3 skills/holy-grail/scripts/scanner.py watch --interval 600 --symbols QQQ,SPY
```

셋업 성립 시 터미널에 `[SETUP]` 강조 표시.

---

## 출력 해석

```
[SETUP] QQQ (NASDAQ-100)
    종가   : 452.30
    20EMA  : 448.12  (거리 +0.94%)
    ADX(14): 34.2 ↑  +DI=28.3
    ★ 셋업 성립 ★
    진입가 (buy stop): 455.80  (전봉 고가 455.35 돌파)
    스톱로스         : 441.20  (리스크 3.2%)
```

- `↑` / `↓` — ADX 상승/하락 방향
- 진입가 = 전봉 고가 × 1.001 (슬리피지 여유)
- 리스크(%) = (진입가 - 스톱) / 진입가

---

## 직장인 실행 가이드

1. **장 마감 후 1회 스캔** (`scan` 모드, 5분 소요)
2. 셋업 성립 시 다음날 **buy stop 주문** 미리 입력
3. 진입 시 **스톱로스 즉시 설정**
4. ADX > 30 유지되는 동안 보유, 하락 전환 시 청산 고려

> **Money Management 우선** — Edge(전략)보다 손실 제한이 생존을 결정한다 (*Street Smarts* 핵심 테제)
