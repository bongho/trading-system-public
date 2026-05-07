#!/usr/bin/env python3
"""
Upbit 단타 v3 라이브 시뮬레이션 (4h 주기)
- 직전 추천 4h P&L 계산 → 새 추천 실행 → JSONL 로그 append
- Telegram Bot API 알림 (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID 환경변수)
- Obsidian 노트 "라이브 시뮬레이션" 섹션 업데이트
"""
import json, os, sys, datetime, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))
from core.reporter import send_telegram, update_obsidian_note
from core import signal_bus
from core.shadow_loop import analyze as shadow_analyze

LOG_DIR   = Path(os.environ.get('LOG_DIR',
                 str(Path(__file__).parent.parent / 'logs')))
RECOMMEND = Path(__file__).parent / 'recommend.py'

# Run shadow_loop analysis every N simulation rounds
_SHADOW_INTERVAL = int(os.environ.get('SHADOW_INTERVAL', '10'))

KST = datetime.timezone(datetime.timedelta(hours=9))


# ── helpers ───────────────────────────────────────────────────────────────────

def fetch_json(url, params=None):
    if params:
        url += '?' + '&'.join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(url, headers={'Accept': 'application/json',
                                               'User-Agent': 'openclaw-sim'})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def upbit_price(market: str) -> float:
    data = fetch_json('https://api.upbit.com/v1/ticker', {'markets': market})
    return float(data[0]['trade_price'])


def log_path(date: datetime.date) -> Path:
    return LOG_DIR / f'upbit_sim_{date.strftime("%Y%m%d")}.jsonl'


def read_last_entry(date: datetime.date) -> dict | None:
    path = log_path(date)
    if not path.exists():
        yesterday = date - datetime.timedelta(days=1)
        path = log_path(yesterday)
    if not path.exists():
        return None
    last = None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                last = json.loads(line)
    return last


def append_log(entry: dict, date: datetime.date):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(log_path(date), 'a') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


# ── recommendation runner ─────────────────────────────────────────────────────

def run_recommend() -> list[dict]:
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, str(RECOMMEND), '--exchange', 'upbit', '--market', 'spot'],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        print(f'[WARN] recommend.py stderr: {result.stderr[:300]}', file=sys.stderr)
    data = json.loads(result.stdout)
    return data.get('top3', [])


# ── P&L from previous round ───────────────────────────────────────────────────

def calc_pnl(prev_recs: list[dict]) -> dict[str, float]:
    pnl = {}
    for r in prev_recs:
        market = r['market']
        entry  = r.get('entry_price') or r.get('price')
        if not entry:
            continue
        try:
            current = upbit_price(market)
            pnl[market] = round((current - entry) / entry * 100, 2)
        except Exception as e:
            print(f'[WARN] P&L fetch failed for {market}: {e}', file=sys.stderr)
    return pnl


# send_telegram is imported from core.reporter


def build_message(ts_kst: str, recs: list[dict], pnl: dict[str, float]) -> str:
    hour = ts_kst[11:16]
    lines = [f'📊 <b>[업비트 단타 추천]</b> {hour} KST\n']

    filter_ok = recs[0].get('filter_passed', True) if recs else True
    label = '🔍 <b>현재 추천 (v3)</b>' if filter_ok else '⚠️ <b>현재 추천 (v3 필터 미달 — 시장 조용)</b>'
    lines.append(label)
    for i, r in enumerate(recs, 1):
        market = r['market']
        lines.append(
            f"{i}. {market} | 1h: {r['change_1h']:+.1f}% | "
            f"15m: {r['change_15m']:+.1f}% | RSI: {r['rsi_14']} | "
            f"급등: {r['vol_ratio']:.1f}×"
        )

    if pnl:
        lines.append('\n📈 <b>이전 4h 성과</b>')
        results, wins = [], 0
        for market, ret in pnl.items():
            emoji = '✅' if ret > 0 else '❌'
            if ret > 0:
                wins += 1
            results.append(f'{market}: {ret:+.2f}% {emoji}')
        lines.append('  '.join(results))
        avg = sum(pnl.values()) / len(pnl)
        lines.append(f'→ 평균 {avg:+.2f}% | 승률 {wins}/{len(pnl)}')

    lines.append('\n#업비트 #스캘핑 #v3')
    return '\n'.join(lines)


# ── Obsidian note update ──────────────────────────────────────────────────────

def _update_obsidian(ts_kst: str, recs: list[dict], pnl: dict[str, float]):
    date_str = ts_kst[:10]
    time_str = ts_kst[11:16]
    rec_str  = ', '.join(r['market'].replace('KRW-', '') for r in recs) if recs else '-'

    avg_pnl = win_str = ''
    if pnl:
        avg     = sum(pnl.values()) / len(pnl)
        wins    = sum(1 for v in pnl.values() if v > 0)
        avg_pnl = f'{avg:+.2f}%'
        win_str = f'{wins}/{len(pnl)}'

    new_row      = f'| {date_str} {time_str} | {rec_str} | {avg_pnl} | {win_str} |'
    table_header = '| 시각 | 추천 코인 | 4h 평균 수익 | 승률 |\n|------|-----------|-------------|------|'

    update_obsidian_note(
        note_path=None,           # reads OBSIDIAN_NOTE_PATH env var
        section_header='## 📡 라이브 시뮬레이션',
        table_header=table_header,
        new_row=new_row,
    )


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    now_kst  = datetime.datetime.now(KST)
    ts_kst   = now_kst.isoformat(timespec='seconds')
    today    = now_kst.date()

    print(f'[{ts_kst}] 시뮬레이션 시작')

    # 1. 직전 추천 → P&L
    last = read_last_entry(today)
    pnl  = {}
    if last and last.get('recs'):
        print(f'[INFO] 직전 추천 {len(last["recs"])}종 P&L 계산 중...')
        pnl = calc_pnl(last['recs'])
        print(f'[INFO] P&L: {pnl}')

    # 2. 새 추천
    print('[INFO] 추천 실행 중...')
    recs = run_recommend()
    print(f'[INFO] 추천 {len(recs)}종: {[r["market"] for r in recs]}')

    if not recs:
        print('[WARN] 추천 결과 없음 — 필터 조건 미충족')

    # 3. 현재가 기록 (다음 라운드 P&L용)
    for r in recs:
        try:
            r['entry_price'] = upbit_price(r['market'])
        except Exception as e:
            print(f'[WARN] entry price fetch failed for {r["market"]}: {e}', file=sys.stderr)

    # 4. JSONL 로그
    entry = {'ts': ts_kst, 'recs': recs, 'prev_pnl': pnl}
    append_log(entry, today)
    print(f'[INFO] 로그 기록: {log_path(today)}')

    # 4b. Signal Bus — emit each rec so skill_bridge can pick them up
    for r in recs:
        signal_bus.emit({
            'symbol':    r['market'],
            'exchange':  'upbit',
            'timeframe': '4h',
            'score':     r.get('score', 50),
            'direction': 'long',
            'entry':     r.get('entry_price'),
            'regime':    'neutral',
            'source':    'upbit_sim_v3',
            'meta': {
                'rsi':        r.get('rsi_14'),
                'vol_ratio':  r.get('vol_ratio'),
                'change_1h':  r.get('change_1h'),
                'change_15m': r.get('change_15m'),
            },
        })
    print(f'[INFO] Signal Bus: {len(recs)}건 emit')

    # 4c. Shadow Loop — run every _SHADOW_INTERVAL rounds
    total_rounds = sum(
        1 for f in LOG_DIR.glob('upbit_sim_*.jsonl')
        for _ in open(f)
        if _.strip()
    )
    shadow_msg = ''
    if total_rounds > 0 and total_rounds % _SHADOW_INTERVAL == 0:
        analysis = shadow_analyze(LOG_DIR)
        print(f'[INFO] Shadow Loop: {analysis.summary_text()}')
        if analysis.status == 'analyzed' and analysis.suggested_params:
            prev = analysis.current_params
            sugg = analysis.suggested_params
            if any(sugg.get(k) != prev.get(k) for k in sugg):
                shadow_msg = (
                    f'\n\n🔬 <b>Shadow Loop 분석</b> ({analysis.total_trades}건)\n'
                    f'승률 {analysis.win_rate:.1f}% | '
                    f'제안 rsi_lo={sugg.get("rsi_lo", "—")} '
                    f'rsi_hi={sugg.get("rsi_hi", "—")} '
                    f'vol_min={sugg.get("vol_ratio_min", "—")}'
                )

    # 5. Telegram
    msg = build_message(ts_kst, recs, pnl) + shadow_msg
    print('\n--- Telegram 메시지 미리보기 ---')
    print(msg)
    print('---')
    send_telegram(msg)

    # 6. Obsidian note
    _update_obsidian(ts_kst, recs, pnl)

    print(f'[{datetime.datetime.now(KST).isoformat(timespec="seconds")}] 완료')


if __name__ == '__main__':
    main()
