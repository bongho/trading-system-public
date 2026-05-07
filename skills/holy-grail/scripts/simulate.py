#!/usr/bin/env python3
"""
Holy Grail 일별 시뮬레이션 (가상 포지션 추적)

매일 22:30 KST (미국 장마감 후) 스케줄러에 의해 실행.
  1. 기존 open 포지션 → 당일 가격으로 P&L 갱신, stop 터치 시 청산
  2. 전체 심볼 스캔 → 신규 셋업 감지 시 watching 추가
  3. watching 포지션 → 진입 조건(고가 >= 진입가) 확인 → open 전환
  4. Telegram 보고 + JSONL 로그 기록

Position 상태:
  watching  — 셋업 감지, buy stop 미체결
  open      — 진입 완료, 추적 중
  stopped   — stop loss 터치 → 청산
  closed    — max_hold 도달 → 청산
  expired   — watching 상태 5일 경과 → 만료

실행:
  python3 simulate.py                      # 기본 (until 2026-05-11)
  python3 simulate.py --until 2026-05-15   # 종료일 지정
  python3 simulate.py --dry-run            # 텔레그램 전송 없이 출력만
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from core.reporter import send_telegram
from scanner import DEFAULT_SYMBOLS, detect_setup, get_ohlcv  # type: ignore

LOG_DIR = Path(os.environ.get(
    "HG_LOG_DIR",
    str(Path(__file__).parents[1] / "logs"),
))
POSITIONS_FILE = LOG_DIR / "positions.jsonl"
MAX_HOLD_DAYS = 10
MAX_WATCHING_DAYS = 5

KST = datetime.timezone(datetime.timedelta(hours=9))


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def load_positions() -> list[dict]:
    if not POSITIONS_FILE.exists():
        return []
    positions = []
    with open(POSITIONS_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    positions.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return positions


def save_positions(positions: list[dict]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        for p in positions:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")


def append_history(entry: dict) -> None:
    """청산된 포지션을 히스토리 파일에 추가한다."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    hist_file = LOG_DIR / "history.jsonl"
    with open(hist_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Price fetcher (latest bar from Yahoo)
# ---------------------------------------------------------------------------

def fetch_latest_bar(symbol: str) -> dict | None:
    try:
        bars = get_ohlcv(symbol, period="5d", interval="1d")
        return bars[-1] if bars else None
    except Exception:
        return None


def fetch_today_price(symbol: str) -> dict | None:
    """Returns dict with open/high/low/close for the latest bar."""
    return fetch_latest_bar(symbol)


# ---------------------------------------------------------------------------
# Position update logic
# ---------------------------------------------------------------------------

def _today_str() -> str:
    return datetime.datetime.now(KST).strftime("%Y-%m-%d")


def update_positions(positions: list[dict]) -> tuple[list[dict], list[dict]]:
    """Update all positions with today's prices.

    Returns (active_positions, just_closed_positions).
    """
    today = _today_str()
    active = []
    closed = []

    for pos in positions:
        status = pos["status"]

        if status in ("stopped", "closed", "expired"):
            active.append(pos)  # 이미 종료된 것은 보존
            continue

        bar = fetch_today_price(pos["symbol"])
        if not bar:
            active.append(pos)
            continue

        cur_close = bar["close"]
        cur_low = bar["low"]
        cur_high = bar["high"]

        if status == "watching":
            # 진입 조건: 당일 고가 >= entry_price → open 전환
            days_watching = (
                datetime.date.fromisoformat(today) -
                datetime.date.fromisoformat(pos["open_date"])
            ).days
            if cur_high >= pos["entry_price"]:
                pos = {**pos, "status": "open", "actual_entry": pos["entry_price"],
                       "trigger_date": today}
                # 즉시 open으로 처리 (이하 open 로직 계속)
            elif days_watching >= MAX_WATCHING_DAYS:
                pos = {**pos, "status": "expired", "exit_date": today,
                       "exit_price": cur_close, "pnl_pct": 0.0}
                closed.append(pos)
                active.append(pos)
                continue
            else:
                active.append(pos)
                continue

        if status == "open" or pos.get("status") == "open":
            # stop 터치 체크
            entry = pos.get("actual_entry") or pos.get("entry_price")
            stop = pos["stop_loss"]

            if cur_low <= stop:
                pnl = round((stop - entry) / entry * 100, 3)
                pos = {**pos, "status": "stopped", "exit_date": today,
                       "exit_price": stop, "pnl_pct": pnl}
                closed.append(pos)
                active.append(pos)
                append_history(pos)
                continue

            # max_hold 체크
            hold_days = (
                datetime.date.fromisoformat(today) -
                datetime.date.fromisoformat(pos.get("trigger_date") or pos["open_date"])
            ).days
            if hold_days >= MAX_HOLD_DAYS:
                pnl = round((cur_close - entry) / entry * 100, 3)
                pos = {**pos, "status": "closed", "exit_date": today,
                       "exit_price": cur_close, "pnl_pct": pnl}
                closed.append(pos)
                active.append(pos)
                append_history(pos)
                continue

            # 보유 중 — P&L 갱신
            pnl = round((cur_close - entry) / entry * 100, 3)
            pos = {**pos, "current_price": cur_close, "pnl_pct": pnl,
                   "hold_days": hold_days, "last_updated": today}
            active.append(pos)

    return active, closed


# ---------------------------------------------------------------------------
# Setup scanner
# ---------------------------------------------------------------------------

def scan_new_setups(
    positions: list[dict],
    symbols: list[dict],
) -> list[dict]:
    """Scan for new setups not already tracked."""
    today = _today_str()
    watching_syms = {p["symbol"] for p in positions if p["status"] in ("watching", "open")}
    new_positions = []

    for entry in symbols:
        sym = entry["symbol"]
        if sym in watching_syms:
            continue
        try:
            bars = get_ohlcv(sym, period="6mo", interval="1d")
            setup = detect_setup(bars)
            if setup.get("setup_active"):
                new_positions.append({
                    "symbol":      sym,
                    "label":       entry["label"],
                    "open_date":   today,
                    "status":      "watching",
                    "entry_price": round(setup["entry_price"], 4),
                    "stop_loss":   round(setup["stop_loss"], 4),
                    "risk_pct":    round(setup["risk_pct"], 2),
                    "adx":         round(setup["adx"], 1),
                    "ema20":       round(setup["ema20"], 4),
                    "trigger_date": None,
                    "actual_entry": None,
                    "current_price": round(bars[-1]["close"], 4),
                    "pnl_pct":     None,
                    "exit_price":  None,
                    "exit_date":   None,
                })
        except Exception:
            continue

    return new_positions


# ---------------------------------------------------------------------------
# Telegram message builder
# ---------------------------------------------------------------------------

def build_message(
    positions: list[dict],
    newly_closed: list[dict],
    new_setups: list[dict],
    today: str,
) -> str:
    lines = [f"📐 <b>[Holy Grail 시뮬레이션]</b> {today}\n"]

    # 신규 셋업
    if new_setups:
        lines.append("🆕 <b>신규 셋업 감지</b>")
        for p in new_setups:
            lines.append(
                f"  {p['label']}  ADX={p['adx']}  "
                f"진입가={p['entry_price']}  SL={p['stop_loss']} (리스크 {p['risk_pct']:.1f}%)"
            )

    # 보유 중 (open + watching)
    active = [p for p in positions if p["status"] in ("open", "watching")]
    if active:
        lines.append("\n📊 <b>보유/대기 포지션</b>")
        for p in active:
            if p["status"] == "open":
                pnl = p.get("pnl_pct", 0) or 0
                emoji = "✅" if pnl >= 0 else "🔴"
                lines.append(
                    f"  {emoji} {p['label']}  현재가={p.get('current_price','?')}  "
                    f"SL={p['stop_loss']}  P&L {pnl:+.2f}%  ({p.get('hold_days',0)}일)"
                )
            else:
                lines.append(
                    f"  ⏳ {p['label']} (대기)  진입가={p['entry_price']}"
                )

    # 오늘 청산
    today_closed = [p for p in newly_closed if p.get("exit_date") == today]
    if today_closed:
        lines.append("\n🏁 <b>오늘 청산</b>")
        total_pnl = 0.0
        for p in today_closed:
            pnl = p.get("pnl_pct", 0) or 0
            total_pnl += pnl
            emoji = "✅" if pnl >= 0 else "❌"
            reason = "스톱" if p["status"] == "stopped" else "만기"
            lines.append(
                f"  {emoji} {p['label']}  {p['entry_price']} → {p['exit_price']}  "
                f"{pnl:+.2f}% ({reason})"
            )
        lines.append(f"  → 오늘 청산 합계: {total_pnl:+.2f}%")

    # 전체 누적 성과 (history 기반)
    hist_file = LOG_DIR / "history.jsonl"
    if hist_file.exists():
        all_pnl = []
        with open(hist_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rec = json.loads(line)
                        if rec.get("pnl_pct") is not None:
                            all_pnl.append(rec["pnl_pct"])
                    except json.JSONDecodeError:
                        continue
        if all_pnl:
            wins = sum(1 for p in all_pnl if p > 0)
            avg = sum(all_pnl) / len(all_pnl)
            lines.append(
                f"\n📈 <b>누적 성과</b> ({len(all_pnl)}건)  "
                f"승률 {wins}/{len(all_pnl)}  평균 {avg:+.2f}%"
            )

    if not active and not today_closed and not new_setups:
        lines.append("\n📭 현재 활성 포지션 없음 — 셋업 대기 중")

    lines.append("\n#HolyGrail #ADX #v3")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Holy Grail 일별 시뮬레이션")
    parser.add_argument("--until", default="2026-05-11", help="시뮬 종료일 YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="텔레그램 전송 없이 출력만")
    parser.add_argument("--symbols", help="쉼표 구분 심볼 (기본: QQQ,SPY,TIGER,KODEX200)")
    args = parser.parse_args()

    today = _today_str()
    print(f"[{today}] Holy Grail 시뮬레이션 시작")

    # 종료일 체크
    try:
        until_date = datetime.date.fromisoformat(args.until)
        if datetime.date.fromisoformat(today) > until_date:
            print(f"[INFO] 시뮬 종료일({until_date}) 경과 — 실행 스킵")
            return
    except ValueError:
        pass

    symbols = DEFAULT_SYMBOLS
    if args.symbols:
        symbols = [{"symbol": s, "label": s} for s in args.symbols.split(",")]

    # 1. 기존 포지션 로드
    positions = load_positions()
    print(f"[INFO] 기존 포지션 {len(positions)}개 로드")

    # 2. 가격 업데이트 & 청산 처리
    positions, newly_closed = update_positions(positions)
    if newly_closed:
        print(f"[INFO] 청산 포지션: {[p['symbol'] for p in newly_closed]}")

    # 3. 신규 셋업 스캔
    new_setups = scan_new_setups(positions, symbols)
    if new_setups:
        print(f"[INFO] 신규 셋업: {[p['symbol'] for p in new_setups]}")
    positions.extend(new_setups)

    # 4. 저장
    save_positions(positions)

    # 5. Telegram 메시지
    msg = build_message(positions, newly_closed, new_setups, today)
    print("\n--- Telegram 미리보기 ---")
    print(msg)
    print("---")

    if not args.dry_run:
        send_telegram(msg)
        print("[INFO] Telegram 전송 완료")
    else:
        print("[DRY-RUN] Telegram 전송 건너뜀")

    print(f"[{datetime.datetime.now(KST).strftime('%H:%M:%S')}] 완료")


if __name__ == "__main__":
    main()
