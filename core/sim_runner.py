"""
범용 일별 시뮬레이션 러너.

모든 스킬/전략이 공통으로 사용하는 포지션 추적 엔진.
positions.jsonl + history.jsonl 로 상태를 영속화한다.

사용법:
  from core.sim_runner import run_daily, format_report, load_positions

  result = run_daily(
      skill_name="my-strat",
      detect_fn=detect_signal,
      symbols=["QQQ", "SPY"],
      max_hold=10,
      until_date="2026-05-11",
  )
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Callable, List, Optional

_SIM_BASE = Path(os.environ.get("SIM_BASE_DIR", "data/simulations"))
KST = datetime.timezone(datetime.timedelta(hours=9))

MAX_WATCHING_DAYS = 5


# ── persistence ───────────────────────────────────────────────────────────────

def _sim_dir(skill_name: str) -> Path:
    d = _SIM_BASE / skill_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_positions(skill_name: str) -> list[dict]:
    f = _sim_dir(skill_name) / "positions.jsonl"
    if not f.exists():
        return []
    out = []
    with open(f, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def save_positions(skill_name: str, positions: list[dict]) -> None:
    f = _sim_dir(skill_name) / "positions.jsonl"
    with open(f, "w", encoding="utf-8") as fh:
        for p in positions:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")


def _append_history(skill_name: str, entry: dict) -> None:
    f = _sim_dir(skill_name) / "history.jsonl"
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_history(skill_name: str) -> list[dict]:
    f = _sim_dir(skill_name) / "history.jsonl"
    if not f.exists():
        return []
    out = []
    with open(f, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


# ── data fetch ────────────────────────────────────────────────────────────────

def _fetch_bar(symbol: str) -> dict | None:
    """최신 일봉 (Yahoo Finance)."""
    import json as _json
    import urllib.request
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol}?range=5d&interval=1d"
    )
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (sim-runner/1.0)",
                      "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = _json.load(r)
        result = data["chart"]["result"][0]
        ts = result["timestamp"]
        q = result["indicators"]["quote"][0]
        bars = []
        for i, t in enumerate(ts):
            o, h, lo, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
            if None in (o, h, lo, c):
                continue
            bars.append({"ts": t, "open": o, "high": h, "low": lo, "close": c})
        return bars[-1] if bars else None
    except Exception:
        return None


def _fetch_history_bars(symbol: str, period: str = "6mo") -> list[dict]:
    import json as _json
    import urllib.request
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol}?range={period}&interval=1d"
    )
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (sim-runner/1.0)",
                      "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = _json.load(r)
        result = data["chart"]["result"][0]
        ts = result["timestamp"]
        q = result["indicators"]["quote"][0]
        bars = []
        for i, t in enumerate(ts):
            o, h, lo, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
            if None in (o, h, lo, c):
                continue
            bars.append({"ts": t, "open": o, "high": h, "low": lo, "close": c})
        return bars
    except Exception:
        return []


# ── position update ───────────────────────────────────────────────────────────

def _today() -> str:
    return datetime.datetime.now(KST).strftime("%Y-%m-%d")


def update_positions(
    skill_name: str,
    positions: list[dict],
    max_hold: int,
) -> tuple[list[dict], list[dict]]:
    """오늘 가격으로 포지션 갱신. Returns (all_positions, newly_closed)."""
    today = _today()
    result_positions = []
    newly_closed = []

    for pos in positions:
        status = pos["status"]
        if status in ("stopped", "closed", "expired"):
            result_positions.append(pos)
            continue

        bar = _fetch_bar(pos["symbol"])
        if not bar:
            result_positions.append(pos)
            continue

        cur_close = bar["close"]
        cur_low = bar["low"]
        cur_high = bar["high"]

        if status == "watching":
            days_w = (
                datetime.date.fromisoformat(today) -
                datetime.date.fromisoformat(pos["open_date"])
            ).days
            if cur_high >= pos["entry_price"]:
                pos = {**pos, "status": "open", "actual_entry": pos["entry_price"],
                       "trigger_date": today}
            elif days_w >= MAX_WATCHING_DAYS:
                pos = {**pos, "status": "expired", "exit_date": today,
                       "exit_price": cur_close, "pnl_pct": 0.0}
                newly_closed.append(pos)
                result_positions.append(pos)
                _append_history(skill_name, pos)
                continue
            else:
                result_positions.append(pos)
                continue

        # open 처리
        entry = pos.get("actual_entry") or pos.get("entry_price")
        stop = pos["stop_loss"]
        trigger_date = pos.get("trigger_date") or pos["open_date"]

        if cur_low <= stop:
            pnl = round((stop - entry) / entry * 100, 3)
            pos = {**pos, "status": "stopped", "exit_date": today,
                   "exit_price": stop, "pnl_pct": pnl}
            newly_closed.append(pos)
            result_positions.append(pos)
            _append_history(skill_name, pos)
            continue

        hold_days = (
            datetime.date.fromisoformat(today) -
            datetime.date.fromisoformat(trigger_date)
        ).days
        if hold_days >= max_hold:
            pnl = round((cur_close - entry) / entry * 100, 3)
            pos = {**pos, "status": "closed", "exit_date": today,
                   "exit_price": cur_close, "pnl_pct": pnl}
            newly_closed.append(pos)
            result_positions.append(pos)
            _append_history(skill_name, pos)
            continue

        pnl = round((cur_close - entry) / entry * 100, 3)
        pos = {**pos, "current_price": cur_close, "pnl_pct": pnl,
               "hold_days": hold_days, "last_updated": today}
        result_positions.append(pos)

    return result_positions, newly_closed


def scan_new_setups(
    skill_name: str,
    detect_fn: Callable,
    symbols: List[str],
    existing_positions: list[dict],
    warmup: int = 60,
) -> list[dict]:
    """신규 셋업 스캔. 이미 watching/open 상태인 심볼은 스킵."""
    today = _today()
    active_syms = {
        p["symbol"] for p in existing_positions
        if p["status"] in ("watching", "open")
    }
    new_positions = []

    for sym in symbols:
        if sym in active_syms:
            continue
        bars = _fetch_history_bars(sym, "6mo")
        if len(bars) < warmup:
            continue
        try:
            sig = detect_fn(bars)
        except Exception:
            continue
        if not sig or not sig.get("active"):
            continue

        cur_close = bars[-1]["close"]
        entry = sig.get("entry_price", cur_close * 1.001)
        stop = sig.get("stop_loss", cur_close * 0.95)

        new_positions.append({
            "symbol":        sym,
            "open_date":     today,
            "status":        "watching",
            "entry_price":   round(entry, 4),
            "stop_loss":     round(stop, 4),
            "risk_pct":      round((entry - stop) / entry * 100, 2),
            "score":         round(sig.get("score", 50), 1),
            "reason":        sig.get("reason", ""),
            "trigger_date":  None,
            "actual_entry":  None,
            "current_price": round(cur_close, 4),
            "pnl_pct":       None,
            "exit_price":    None,
            "exit_date":     None,
            "hold_days":     0,
            "last_updated":  today,
        })

    return new_positions


# ── main entry ────────────────────────────────────────────────────────────────

def run_daily(
    skill_name: str,
    detect_fn: Callable,
    symbols: List[str],
    max_hold: int = 10,
    warmup: int = 60,
    until_date: str = "",
    dry_run: bool = False,
) -> dict:
    """하루치 시뮬레이션 실행. Returns status dict."""
    today = _today()

    if until_date:
        try:
            if datetime.date.fromisoformat(today) > datetime.date.fromisoformat(until_date):
                return {"status": "expired", "reason": f"past until_date {until_date}"}
        except ValueError:
            pass

    positions = load_positions(skill_name)
    positions, newly_closed = update_positions(skill_name, positions, max_hold)
    new_setups = scan_new_setups(skill_name, detect_fn, symbols, positions, warmup)
    positions.extend(new_setups)

    if not dry_run:
        save_positions(skill_name, positions)

    active = [p for p in positions if p["status"] in ("open", "watching")]
    hist = load_history(skill_name)
    pnl_list = [h.get("pnl_pct", 0) for h in hist if h.get("pnl_pct") is not None]

    agg = {}
    if pnl_list:
        wins = sum(1 for v in pnl_list if v > 0)
        agg = {
            "total_trades": len(pnl_list),
            "win_rate":     round(wins / len(pnl_list) * 100, 1),
            "avg_pnl":      round(sum(pnl_list) / len(pnl_list), 3),
            "total_pnl":    round(sum(pnl_list), 2),
        }

    return {
        "status":        "ok",
        "skill_name":    skill_name,
        "today":         today,
        "active":        active,
        "newly_closed":  newly_closed,
        "new_setups":    new_setups,
        "aggregate":     agg,
    }


def format_telegram(result: dict) -> str:
    """run_daily 결과를 텔레그램 메시지로 포맷."""
    if result.get("status") == "expired":
        return f"📐 시뮬레이션 종료 ({result.get('reason', '')})"

    name = result.get("skill_name", "")
    today = result.get("today", "")
    lines = [f"📐 <b>[{name}]</b> {today}\n"]

    new_setups = result.get("new_setups", [])
    if new_setups:
        lines.append("🆕 <b>신규 셋업</b>")
        for p in new_setups:
            lines.append(
                f"  {p['symbol']}  진입={p['entry_price']}  "
                f"SL={p['stop_loss']} ({p['risk_pct']:.1f}%)  {p.get('reason', '')[:50]}"
            )

    active = result.get("active", [])
    if active:
        lines.append("\n📊 <b>보유/대기</b>")
        for p in active:
            if p["status"] == "open":
                pnl = p.get("pnl_pct", 0) or 0
                em = "✅" if pnl >= 0 else "🔴"
                lines.append(
                    f"  {em} {p['symbol']}  현재가={p.get('current_price', '?')}  "
                    f"P&L <b>{pnl:+.2f}%</b>  ({p.get('hold_days', 0)}일)"
                )
            else:
                lines.append(f"  ⏳ {p['symbol']} 대기 → 진입={p['entry_price']}")

    newly_closed = result.get("newly_closed", [])
    today_closed = [p for p in newly_closed if p.get("exit_date") == today]
    if today_closed:
        lines.append("\n🏁 <b>오늘 청산</b>")
        for p in today_closed:
            pnl = p.get("pnl_pct", 0) or 0
            em = "✅" if pnl >= 0 else "❌"
            reason = "스톱" if p["status"] == "stopped" else "만기"
            lines.append(
                f"  {em} {p['symbol']}  {p.get('actual_entry') or p['entry_price']}"
                f" → {p['exit_price']}  {pnl:+.2f}% ({reason})"
            )

    agg = result.get("aggregate", {})
    if agg:
        lines.append(
            f"\n📈 <b>누적</b> {agg['total_trades']}건  "
            f"승률 {agg['win_rate']:.1f}%  평균 {agg['avg_pnl']:+.2f}%  "
            f"합계 {agg['total_pnl']:+.2f}%"
        )

    if not active and not today_closed and not new_setups:
        lines.append("📭 활성 포지션 없음")

    return "\n".join(lines)
