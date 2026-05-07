"""Holy Grail 시뮬레이션 Telegram 핸들러.

명령어:
  /hg         — 현재 포지션 + 누적 성과
  /hg_scan    — 즉시 스캔 + 포지션 업데이트 실행
  /hg_bt      — 백테스트 결과 요약 (마지막 실행 기준)
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

_SKILLS_DIR = Path("/app/skills/holy-grail/scripts")
_LOG_DIR = Path(os.environ.get("HG_LOG_DIR", "/app/skills/holy-grail/logs"))


def register_hg_handlers(bot: "TradingBot") -> None:
    from telegram.ext import CommandHandler

    app = bot.app
    app.add_handler(CommandHandler("hg", _hg_status))
    app.add_handler(CommandHandler("hg_scan", _hg_scan))
    app.add_handler(CommandHandler("hg_bt", _hg_backtest))


# ---------------------------------------------------------------------------
# /hg — 현재 포지션 조회
# ---------------------------------------------------------------------------

@authorized_only
async def _hg_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    positions_file = _LOG_DIR / "positions.jsonl"
    history_file = _LOG_DIR / "history.jsonl"

    if not positions_file.exists():
        await update.message.reply_text("📭 Holy Grail 시뮬레이션 데이터 없음 (아직 실행되지 않음)")
        return

    positions = _load_jsonl(positions_file)
    active = [p for p in positions if p["status"] in ("open", "watching")]
    ended = [p for p in positions if p["status"] in ("stopped", "closed", "expired")]

    lines = ["📐 <b>Holy Grail 시뮬레이션 현황</b>\n"]

    if active:
        lines.append("<b>보유/대기 포지션</b>")
        for p in active:
            if p["status"] == "open":
                pnl = p.get("pnl_pct", 0) or 0
                emoji = "✅" if pnl >= 0 else "🔴"
                lines.append(
                    f"{emoji} {p.get('label', p['symbol'])} "
                    f"진입={p.get('actual_entry') or p['entry_price']}  "
                    f"SL={p['stop_loss']}  P&L <b>{pnl:+.2f}%</b> ({p.get('hold_days', 0)}일)"
                )
            else:
                lines.append(
                    f"⏳ {p.get('label', p['symbol'])} (매수 대기)  "
                    f"buy stop {p['entry_price']}"
                )
    else:
        lines.append("현재 활성 포지션 없음")

    # 누적 성과 (history)
    if history_file.exists():
        hist = _load_jsonl(history_file)
        pnl_list = [h.get("pnl_pct", 0) for h in hist if h.get("pnl_pct") is not None]
        if pnl_list:
            wins = sum(1 for v in pnl_list if v > 0)
            avg = sum(pnl_list) / len(pnl_list)
            total = sum(pnl_list)
            lines.append(
                f"\n<b>누적 성과</b> {len(pnl_list)}거래 | "
                f"승률 {wins}/{len(pnl_list)} | "
                f"평균 {avg:+.2f}% | 합계 {total:+.2f}%"
            )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ---------------------------------------------------------------------------
# /hg_scan — 즉시 스캔 실행
# ---------------------------------------------------------------------------

@authorized_only
async def _hg_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    script = _SKILLS_DIR / "simulate.py"
    if not script.exists():
        await update.message.reply_text("⚠ simulate.py 스크립트를 찾을 수 없습니다")
        return

    await update.message.reply_text("⏳ Holy Grail 스캔 실행 중...")
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script), "--dry-run",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        output = stdout.decode()[:2000] if stdout else ""
        if stderr:
            logger.warning("hg_scan stderr: %s", stderr.decode()[:300])

        if "신규 셋업" in output or "청산" in output:
            await update.message.reply_text(f"✅ 스캔 완료\n<pre>{output[:1500]}</pre>",
                                             parse_mode="HTML")
        else:
            await update.message.reply_text("✅ 스캔 완료 — 신규 셋업 없음")
    except asyncio.TimeoutError:
        await update.message.reply_text("⚠ 스캔 시간 초과 (120초)")
    except Exception as e:
        await update.message.reply_text(f"⚠ 오류: {e}")


# ---------------------------------------------------------------------------
# /hg_bt — 백테스트 결과 조회
# ---------------------------------------------------------------------------

@authorized_only
async def _hg_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    script = _SKILLS_DIR / "backtest.py"
    if not script.exists():
        await update.message.reply_text("⚠ backtest.py 스크립트를 찾을 수 없습니다")
        return

    await update.message.reply_text("⏳ Holy Grail 백테스트 실행 중 (1~2분)...")
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script), "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        if not stdout:
            await update.message.reply_text("⚠ 백테스트 결과 없음")
            return

        results = json.loads(stdout.decode())
        lines = ["📊 <b>Holy Grail 백테스트 결과</b>\n"]
        for r in results:
            if "error" in r:
                lines.append(f"⚠ {r['symbol']}: {r['error']}")
                continue
            m = r.get("metrics", {})
            p_val = m.get("monte_carlo_pvalue", 1.0)
            sig = "✅" if p_val < 0.05 else ("⚠" if p_val < 0.15 else "❌")
            lines.append(
                f"<b>{r['symbol']}</b> {len(r.get('trades', []))}거래\n"
                f"  승률 {m.get('win_rate', 0):.1f}%  "
                f"평균 {m.get('avg_ret_pct', 0):+.3f}%  "
                f"샤프 {m.get('sharpe', 0):.2f}\n"
                f"  MC p={p_val:.3f} {sig}  "
                f"WF {r.get('wf_consistent', '')}"
            )

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except asyncio.TimeoutError:
        await update.message.reply_text("⚠ 백테스트 시간 초과 (300초)")
    except Exception as e:
        await update.message.reply_text(f"⚠ 오류: {e}")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _load_jsonl(path: Path) -> list[dict]:
    results = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return results
