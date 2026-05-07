"""
Signal Bus — file-based IPC between stdlib skills and the async src/ layer.

Dependency hierarchy (no circular imports):
  src/engine/skill_bridge.py  →  core/signal_bus.py  ←  skills/*.py

Skills write Signal dicts to data/signals/pending.jsonl.
skill_bridge.py drains the bus and routes signals through SwarmConsensus.

This keeps skills as self-contained stdlib scripts while allowing them to
participate in the full agent pipeline when the trading engine is running.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

_DEFAULT_BUS_DIR = Path(os.environ.get('SIGNAL_BUS_DIR', 'data/signals'))

PENDING_FILE   = 'pending.jsonl'
PROCESSED_FILE = 'processed.jsonl'
REJECTED_FILE  = 'rejected.jsonl'


def emit(signal_dict: dict, bus_dir: Path = _DEFAULT_BUS_DIR) -> None:
    """Append a signal to the pending queue.

    Skills call this after generating a recommendation.
    Thread-safe for append-only writes on the same host.
    """
    bus_dir.mkdir(parents=True, exist_ok=True)
    signal_dict = {**signal_dict, '_emitted_at': datetime.now(timezone.utc).isoformat()}
    with open(bus_dir / PENDING_FILE, 'a', encoding='utf-8') as f:
        f.write(json.dumps(signal_dict, ensure_ascii=False) + '\n')


def drain(bus_dir: Path = _DEFAULT_BUS_DIR) -> Iterator[dict]:
    """Yield and atomically remove all pending signals.

    Called by skill_bridge on each scheduling tick.
    Processed signals are archived to processed.jsonl for audit.
    """
    pending = bus_dir / PENDING_FILE
    if not pending.exists():
        return

    with open(pending, encoding='utf-8') as f:
        lines = [l.strip() for l in f if l.strip()]

    pending.unlink()

    if not lines:
        return

    with open(bus_dir / PROCESSED_FILE, 'a', encoding='utf-8') as f:
        for line in lines:
            f.write(line + '\n')

    for line in lines:
        yield json.loads(line)


def reject(signal_dict: dict, reason: str, bus_dir: Path = _DEFAULT_BUS_DIR) -> None:
    """Archive a signal that was rejected by SwarmConsensus or SkillBridge."""
    bus_dir.mkdir(parents=True, exist_ok=True)
    entry = {**signal_dict, '_rejected_at': datetime.now(timezone.utc).isoformat(),
             '_rejection_reason': reason}
    with open(bus_dir / REJECTED_FILE, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def peek(bus_dir: Path = _DEFAULT_BUS_DIR) -> list[dict]:
    """Read pending signals without consuming them (for monitoring)."""
    pending = bus_dir / PENDING_FILE
    if not pending.exists():
        return []
    with open(pending, encoding='utf-8') as f:
        return [json.loads(l) for l in f if l.strip()]


def pending_count(bus_dir: Path = _DEFAULT_BUS_DIR) -> int:
    pending = bus_dir / PENDING_FILE
    if not pending.exists():
        return 0
    with open(pending, encoding='utf-8') as f:
        return sum(1 for l in f if l.strip())
