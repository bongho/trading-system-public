"""
전략 빌더 Telegram 핸들러.

명령어:
  /build <전략 설명>   — LLM으로 전략 생성 → 코드 미리보기 → 저장
  /confirm             — 마지막 생성 전략 저장 확인
  /reject              — 마지막 생성 전략 폐기
  /bt <skill_name>     — 범용 백테스트 실행 (custom 포함)
  /sim <name> start    — 시뮬레이션 시작
  /sim <name> stop     — 시뮬레이션 정지
  /sim <name> status   — 단일 시뮬 현황
  /sims                — 전체 실행 중 시뮬레이션 목록
  /skills              — 사용 가능한 스킬 전체 목록
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from src.telegram.middleware import authorized_only

if TYPE_CHECKING:
    from src.telegram.bot import TradingBot

logger = logging.getLogger(__name__)

_ROOT = Path("/app")
_CUSTOM_DIR = _ROOT / "skills" / "custom"
_SIM_BASE = Path(os.environ.get("SIM_BASE_DIR", "/app/data/simulations"))
_SIM_REGISTRY = _SIM_BASE / "_registry.json"

# 채팅별 대기 중인 전략 (chat_id → generate() 결과)
_pending: dict[int, dict] = {}


def register_builder_handlers(bot: "TradingBot") -> None:
    from telegram.ext import CommandHandler

    app = bot.app
    app.add_handler(CommandHandler("build", _build))
    app.add_handler(CommandHandler("confirm", _confirm))
    app.add_handler(CommandHandler("reject", _reject))
    app.add_handler(CommandHandler("bt", _bt))
    app.add_handler(CommandHandler("sim", _sim))
    app.add_handler(CommandHandler("sims", _sims))
    app.add_handler(CommandHandler("skills", _skills))


# ---------------------------------------------------------------------------
# /build <description>
# ---------------------------------------------------------------------------

@authorized_only
async def _build(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    description = " ".join(context.args or []).strip()
    if not description:
        await update.message.reply_text(
            "사용법: /build <전략 설명>\n\n"
            "예) /build QQQ ADX가 30 초과하고 상승 중일 때 20EMA에 풀백하면 매수, "
            "10일 후 청산"
        )
        return

    await update.message.reply_text("⏳ 전략 생성 중... (약 15초)")

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, _generate_sync, description
        )
    except Exception as e:
        await update.message.reply_text(f"❌ 전략 생성 실패\n<code>{e}</code>",
                                         parse_mode="HTML")
        return

    chat_id = update.effective_chat.id
    _pending[chat_id] = result

    # 코드 미리보기 (앞 30줄)
    preview_lines = result["code"].splitlines()[:30]
    preview = "\n".join(preview_lines)
    if len(result["code"].splitlines()) > 30:
        preview += "\n... (생략)"

    msg = (
        f"✅ <b>{result['name']}</b> 생성 완료\n\n"
        f"📋 {result['description']}\n"
        f"심볼: {', '.join(result['symbols'])}  "
        f"보유: {result['hold_days']}일\n\n"
        f"<pre>{preview[:2000]}</pre>\n\n"
        "저장하려면 /confirm, 폐기하려면 /reject"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


def _generate_sync(description: str) -> dict:
    """executor에서 실행되는 동기 래퍼."""
    _ROOT_SYS = Path(__file__).parents[3]
    if str(_ROOT_SYS) not in sys.path:
        sys.path.insert(0, str(_ROOT_SYS))
    from core.strategy_generator import generate
    return generate(description)


# ---------------------------------------------------------------------------
# /confirm / /reject
# ---------------------------------------------------------------------------

@authorized_only
async def _confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    result = _pending.pop(chat_id, None)
    if not result:
        await update.message.reply_text("❌ 대기 중인 전략이 없습니다. /build 로 먼저 생성하세요.")
        return

    try:
        from core.strategy_generator import save_strategy
        path = save_strategy(result, base_dir=_CUSTOM_DIR)
    except Exception as e:
        await update.message.reply_text(f"❌ 저장 실패: {e}")
        return

    await update.message.reply_text(
        f"✅ <b>{result['name']}</b> 저장 완료\n"
        f"경로: <code>{path}</code>\n\n"
        f"백테스트: /bt {result['name']}\n"
        f"시뮬레이션: /sim {result['name']} start",
        parse_mode="HTML",
    )


@authorized_only
async def _reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    result = _pending.pop(chat_id, None)
    name = result["name"] if result else "없음"
    await update.message.reply_text(f"🗑 전략 '{name}' 폐기됨")


# ---------------------------------------------------------------------------
# /bt <skill_name> [period] [symbols]
# ---------------------------------------------------------------------------

@authorized_only
async def _bt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "사용법: /bt <skill_name> [period] [symbol1,symbol2]\n\n"
            "예) /bt holy-grail\n"
            "    /bt ema-rsi-cross 1y QQQ,SPY,TQQQ"
        )
        return

    skill_name = args[0]
    period = args[1] if len(args) > 1 else "2y"
    symbols_arg = args[2].split(",") if len(args) > 2 else None

    strategy_path = _resolve_skill(skill_name)
    if not strategy_path:
        await update.message.reply_text(
            f"❌ '{skill_name}' 스킬을 찾을 수 없습니다.\n/skills 로 목록 확인"
        )
        return

    await update.message.reply_text(
        f"⏳ <b>{skill_name}</b> 백테스트 실행 중 ({period})...",
        parse_mode="HTML",
    )

    try:
        bt_result = await asyncio.get_event_loop().run_in_executor(
            None, _backtest_sync, strategy_path, symbols_arg, period
        )
    except Exception as e:
        await update.message.reply_text(f"❌ 백테스트 실패\n<code>{e}</code>",
                                         parse_mode="HTML")
        return

    from core.backtest_runner import format_report
    report = format_report(bt_result, include_trades=True)
    await update.message.reply_text(
        f"📊 <b>{skill_name}</b> 백테스트 결과\n\n{report}",
        parse_mode="HTML",
    )


def _backtest_sync(strategy_path: Path, symbols: list | None, period: str) -> dict:
    _ROOT_SYS = Path(__file__).parents[3]
    if str(_ROOT_SYS) not in sys.path:
        sys.path.insert(0, str(_ROOT_SYS))
    from core.backtest_runner import run_from_file
    kwargs = {"period": period}
    if symbols:
        kwargs["symbols"] = symbols
    return run_from_file(strategy_path, **kwargs)


# ---------------------------------------------------------------------------
# /sim <name> start|stop|status
# ---------------------------------------------------------------------------

@authorized_only
async def _sim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "사용법:\n"
            "  /sim <name> start [until YYYY-MM-DD]\n"
            "  /sim <name> stop\n"
            "  /sim <name> status\n"
            "  /sim <name> run  ← 즉시 1회 실행\n\n"
            "예) /sim holy-grail start until 2026-05-11\n"
            "    /sim ema-rsi-cross start"
        )
        return

    skill_name, subcommand = args[0], args[1].lower()

    if subcommand == "start":
        until = ""
        if len(args) >= 4 and args[2].lower() == "until":
            until = args[3]
        _sim_start(skill_name, until)
        await update.message.reply_text(
            f"▶️ <b>{skill_name}</b> 시뮬레이션 시작\n"
            f"{'종료일: ' + until if until else '종료일: 없음 (무기한)'}\n"
            "매일 22:30 KST 자동 실행\n\n"
            f"현황: /sim {skill_name} status",
            parse_mode="HTML",
        )
    elif subcommand == "stop":
        _sim_stop(skill_name)
        await update.message.reply_text(f"⏹ <b>{skill_name}</b> 시뮬레이션 정지됨",
                                         parse_mode="HTML")
    elif subcommand in ("status", "stat"):
        text = await asyncio.get_event_loop().run_in_executor(
            None, _sim_status_text, skill_name
        )
        await update.message.reply_text(text, parse_mode="HTML")
    elif subcommand == "run":
        # 즉시 1회 실행
        strategy_path = _resolve_skill(skill_name)
        if not strategy_path:
            await update.message.reply_text(f"❌ '{skill_name}' 스킬 없음")
            return
        await update.message.reply_text(f"⏳ {skill_name} 시뮬레이션 실행 중...")
        text = await asyncio.get_event_loop().run_in_executor(
            None, _run_sim_once, skill_name, strategy_path
        )
        await update.message.reply_text(text, parse_mode="HTML")
    else:
        await update.message.reply_text(f"❓ 알 수 없는 서브커맨드: {subcommand}")


def _sim_start(skill_name: str, until: str = "") -> None:
    registry = _load_registry()
    registry[skill_name] = {"enabled": True, "until": until}
    _save_registry(registry)


def _sim_stop(skill_name: str) -> None:
    registry = _load_registry()
    if skill_name in registry:
        registry[skill_name]["enabled"] = False
        _save_registry(registry)


def _sim_status_text(skill_name: str) -> str:
    _ROOT_SYS = Path(__file__).parents[3]
    if str(_ROOT_SYS) not in sys.path:
        sys.path.insert(0, str(_ROOT_SYS))
    from core.sim_runner import load_history, load_positions

    positions = load_positions(skill_name)
    hist = load_history(skill_name)
    pnl_list = [h.get("pnl_pct", 0) for h in hist if h.get("pnl_pct") is not None]
    active = [p for p in positions if p["status"] in ("open", "watching")]

    lines = [f"📐 <b>{skill_name}</b> 시뮬레이션 현황\n"]
    if active:
        lines.append("<b>보유/대기</b>")
        for p in active:
            if p["status"] == "open":
                pnl = p.get("pnl_pct", 0) or 0
                em = "✅" if pnl >= 0 else "🔴"
                lines.append(
                    f"  {em} {p['symbol']}  P&L <b>{pnl:+.2f}%</b>"
                    f"  ({p.get('hold_days', 0)}일)"
                )
            else:
                lines.append(f"  ⏳ {p['symbol']} 대기 → 진입={p['entry_price']}")
    else:
        lines.append("활성 포지션 없음")

    if pnl_list:
        wins = sum(1 for v in pnl_list if v > 0)
        avg = sum(pnl_list) / len(pnl_list)
        lines.append(
            f"\n<b>누적</b> {len(pnl_list)}건  "
            f"승률 {wins}/{len(pnl_list)}  평균 {avg:+.2f}%"
        )
    return "\n".join(lines)


def _run_sim_once(skill_name: str, strategy_path: Path) -> str:
    _ROOT_SYS = Path(__file__).parents[3]
    if str(_ROOT_SYS) not in sys.path:
        sys.path.insert(0, str(_ROOT_SYS))
    import importlib.util
    from core.sim_runner import format_telegram, run_daily

    spec = importlib.util.spec_from_file_location("_strat", strategy_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    detect_fn = getattr(mod, "detect_signal")
    meta: dict = getattr(mod, "METADATA", {})
    syms = meta.get("symbols", ["QQQ", "SPY"])
    max_hold = meta.get("hold_days", 10)
    warmup = meta.get("warmup_bars", 60)

    registry = _load_registry()
    until = registry.get(skill_name, {}).get("until", "")

    result = run_daily(skill_name, detect_fn, syms, max_hold=max_hold,
                       warmup=warmup, until_date=until)
    return format_telegram(result)


# ---------------------------------------------------------------------------
# /sims
# ---------------------------------------------------------------------------

@authorized_only
async def _sims(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    registry = _load_registry()
    if not registry:
        await update.message.reply_text("📭 실행 중인 시뮬레이션 없음\n/sim <name> start 로 시작")
        return

    lines = ["📐 <b>시뮬레이션 목록</b>\n"]
    for name, info in registry.items():
        enabled = info.get("enabled", False)
        until = info.get("until", "")
        em = "▶️" if enabled else "⏹"
        lines.append(
            f"{em} <b>{name}</b>"
            + (f"  until {until}" if until else "")
        )
    lines.append("\n명령: /sim <name> status | start | stop | run")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ---------------------------------------------------------------------------
# /skills
# ---------------------------------------------------------------------------

@authorized_only
async def _skills(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = ["📋 <b>사용 가능한 스킬</b>\n"]

    # 기본 스킬
    lines.append("<b>기본 제공</b>")
    builtin = [
        ("holy-grail",  "Street Smarts ADX+20EMA 풀백 (일봉)"),
        ("second-play", "Aschenbrenner 2차 병목 스크리너 (US 주식)"),
        ("crypto-recommender", "업비트/바이낸스 스캘핑 신호"),
    ]
    for name, desc in builtin:
        lines.append(f"  • <b>{name}</b>: {desc}")

    # 커스텀 스킬
    if _CUSTOM_DIR.exists():
        customs = sorted(
            d for d in _CUSTOM_DIR.iterdir()
            if d.is_dir() and (d / "strategy.py").exists()
        )
        if customs:
            lines.append("\n<b>사용자 생성</b>")
            for d in customs:
                meta = _read_meta(d / "strategy.py")
                desc = meta.get("description", "")[:50]
                syms = ", ".join(meta.get("symbols", []))
                lines.append(f"  • <b>{d.name}</b>: {desc} [{syms}]")

    lines.append(
        "\n백테스트: /bt <name>\n"
        "시뮬레이션: /sim <name> start\n"
        "새 전략 생성: /build <설명>"
    )
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _resolve_skill(name: str) -> Path | None:
    """스킬 이름 → strategy.py 경로."""
    # 커스텀
    custom = _CUSTOM_DIR / name / "strategy.py"
    if custom.exists():
        return custom
    # 기본 스킬 — scanner/screen 계열
    for subpath in [
        f"skills/{name}/scripts/scanner.py",
        f"skills/{name}/scripts/screen.py",
        f"skills/{name}/scripts/strategy.py",
    ]:
        p = _ROOT / subpath
        if p.exists():
            return p
    return None


def _read_meta(path: Path) -> dict:
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_m", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, "METADATA", {})
    except Exception:
        return {}


def _load_registry() -> dict:
    _SIM_BASE.mkdir(parents=True, exist_ok=True)
    if not _SIM_REGISTRY.exists():
        return {}
    try:
        return json.loads(_SIM_REGISTRY.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_registry(data: dict) -> None:
    _SIM_BASE.mkdir(parents=True, exist_ok=True)
    _SIM_REGISTRY.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
