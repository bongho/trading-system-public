#!/usr/bin/env python3
"""
Upbit 단타 v3 라이브 시뮬레이션 (4h 주기)
- 직전 추천 4h P&L 계산 → 새 추천 실행 → JSONL 로그 append
- Telegram Bot API 알림 (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID 환경변수)
- Obsidian 노트 "라이브 시뮬레이션" 섹션 업데이트
"""
import json, os, sys, time, datetime, urllib.request, urllib.parse
from pathlib import Path

LOG_DIR   = Path(os.environ.get('LOG_DIR',
                 str(Path(__file__).parent.parent / 'logs')))
_note_env = os.environ.get('OBSIDIAN_NOTE_PATH', '')
NOTE_PATH = Path(_note_env) if _note_env else None
RECOMMEND = Path(__file__).parent / 'recommend.py'

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


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(text: str):
    token   = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        print('[INFO] Telegram env vars not set — skipping notification')
        return
    url  = f'https://api.telegram.org/bot{token}/sendMessage'
    body = json.dumps({'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}).encode()
    req  = urllib.request.Request(url, data=body,
                                  headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.load(r)
        if resp.get('ok'):
            print('[INFO] Telegram message sent')
        else:
            print(f'[WARN] Telegram error: {resp}', file=sys.stderr)
    except Exception as e:
        print(f'[WARN] Telegram send failed: {e}', file=sys.stderr)


def build_message(ts_kst: str, recs: list[dict], pnl: dict[str, float]) -> str:
    hour = ts_kst[11:16]
    lines = [f'📊 <b>[업비트 단타 추천]</b> {hour} KST\n']

    lines.append('🔍 <b>현재 추천 (v3)</b>')
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

def update_obsidian_note(ts_kst: str, recs: list[dict], pnl: dict[str, float]):
    if NOTE_PATH is None:
        return
    if not NOTE_PATH.exists():
        print(f'[WARN] Note not found: {NOTE_PATH}')
        return

    content = NOTE_PATH.read_text(encoding='utf-8')
    section_header = '## 📡 라이브 시뮬레이션'

    # build new row
    date_str = ts_kst[:10]
    time_str = ts_kst[11:16]
    rec_str = ', '.join(r['market'].replace('KRW-', '') for r in recs) if recs else '-'

    avg_pnl = ''
    win_str = ''
    if pnl:
        avg = sum(pnl.values()) / len(pnl)
        wins = sum(1 for v in pnl.values() if v > 0)
        avg_pnl = f'{avg:+.2f}%'
        win_str = f'{wins}/{len(pnl)}'

    new_row = f'| {date_str} {time_str} | {rec_str} | {avg_pnl} | {win_str} |'

    if section_header not in content:
        table_header = (
            f'\n\n{section_header}\n\n'
            '| 시각 | 추천 코인 | 4h 평균 수익 | 승률 |\n'
            '|------|-----------|-------------|------|\n'
        )
        content = content.rstrip() + table_header + new_row + '\n'
    else:
        # append after last table row
        idx = content.rfind('| ')
        if idx != -1:
            end = content.find('\n', idx)
            content = content[:end + 1] + new_row + '\n' + content[end + 1:]
        else:
            content += new_row + '\n'

    NOTE_PATH.write_text(content, encoding='utf-8')
    print(f'[INFO] Obsidian note updated: {new_row}')


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

    # 5. Telegram
    msg = build_message(ts_kst, recs, pnl)
    print('\n--- Telegram 메시지 미리보기 ---')
    print(msg)
    print('---')
    send_telegram(msg)

    # 6. Obsidian note
    update_obsidian_note(ts_kst, recs, pnl)

    print(f'[{datetime.datetime.now(KST).isoformat(timespec="seconds")}] 완료')


if __name__ == '__main__':
    main()
