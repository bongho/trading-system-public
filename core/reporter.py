"""
Shared reporter for trading skills (stdlib-only).

Handles two optional sinks:
  1. Telegram Bot API  — env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  2. Obsidian markdown — env: OBSIDIAN_NOTE_PATH (or passed explicitly)

Both sinks are no-ops when their config is absent, so skills remain
portable to environments without Telegram or Obsidian.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Optional


def send_telegram(
    text: str,
    token: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> None:
    token   = token   or os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = chat_id or os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        print('[INFO] Telegram env vars not set — skipping notification')
        return
    url  = f'https://api.telegram.org/bot{token}/sendMessage'
    body = json.dumps({'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}).encode()
    req  = urllib.request.Request(
        url, data=body, headers={'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.load(r)
        if resp.get('ok'):
            print('[INFO] Telegram message sent')
        else:
            print(f'[WARN] Telegram error: {resp}', file=sys.stderr)
    except Exception as e:
        print(f'[WARN] Telegram send failed: {e}', file=sys.stderr)


def update_obsidian_note(
    note_path: Optional[str | Path],
    section_header: str,
    table_header: str,
    new_row: str,
) -> None:
    """Append new_row to an Obsidian markdown table under section_header.

    Creates the section + table if it doesn't exist yet.
    No-ops when note_path is None or the file doesn't exist.
    """
    if note_path is None:
        note_path = os.environ.get('OBSIDIAN_NOTE_PATH', '')
    if not note_path:
        return

    path = Path(note_path)
    if not path.exists():
        print(f'[WARN] Obsidian note not found: {path}', file=sys.stderr)
        return

    content = path.read_text(encoding='utf-8')

    if section_header not in content:
        content = content.rstrip() + (
            f'\n\n{section_header}\n\n{table_header}\n{new_row}\n'
        )
    else:
        idx = content.rfind('| ')
        if idx != -1:
            end = content.find('\n', idx)
            content = content[:end + 1] + new_row + '\n' + content[end + 1:]
        else:
            content += new_row + '\n'

    path.write_text(content, encoding='utf-8')
    print(f'[INFO] Obsidian note updated: {new_row}')
