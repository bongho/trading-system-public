"""
텍스트 설명 → 트레이딩 전략 코드 생성기.

Claude 또는 OpenAI API를 사용해 detect_signal 인터페이스를 구현한
Python 코드를 생성한다. 생성된 코드는 syntax 검사 + dry-run 검증을 거친다.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
import textwrap
import urllib.request
from pathlib import Path

_SYSTEM_PROMPT = """\
You are an expert trading strategy developer. Your task is to generate a Python \
trading strategy following EXACTLY the interface below.

## Required interface

```python
from core.indicators import (
    calc_ema, calc_sma, calc_rsi, calc_atr,
    calc_macd, calc_bb, calc_adx,
    find_swing_low, find_swing_high,
)

METADATA = {
    "name": "strategy-name",   # kebab-case slug, e.g. "ema-rsi-cross"
    "description": "one sentence",
    "symbols": ["QQQ", "SPY"], # default watchlist
    "timeframe": "1d",
    "hold_days": 10,           # typical holding period in calendar days
    "warmup_bars": 60,         # minimum bars before first signal
}

def detect_signal(bars: list[dict]) -> dict | None:
    \"\"\"
    bars: list sorted oldest-first, each bar = {ts, open, high, low, close}
    Returns None (no signal) or:
    {
        "active": True,
        "entry_price": float,   # buy-stop level (above current close)
        "stop_loss": float,     # invalidation (strictly below entry_price)
        "score": float,         # signal strength 0-100
        "reason": str,          # short human-readable explanation
    }
    \"\"\"
```

## Constraints
1. Only import from `core.indicators` — NO other imports allowed.
2. No network calls, no file I/O, no subprocess.
3. Guard against insufficient data (return None if len(bars) < METADATA['warmup_bars']).
4. entry_price must be above current close (buy-stop pattern).
5. stop_loss must be strictly below entry_price.
6. Keep detect_signal under 40 lines of logic.

## Available indicators (from core.indicators)
- calc_ema(closes, period) → list[float]       (EMA series, nan-padded)
- calc_sma(closes, period) → list[float]
- calc_rsi(closes, period=14) → float          (latest RSI value)
- calc_atr(bars, period=14) → float            (latest ATR value)
- calc_macd(closes, fast=12, slow=26, signal=9) → (macd, signal, histogram)
- calc_bb(closes, period=20, std_dev=2.0) → (upper, mid, lower)
- calc_adx(bars, period=14) → {adx, plus_di, minus_di, adx_rising}
- find_swing_low(bars, lookback=5) → float
- find_swing_high(bars, lookback=5) → float

## Output format
Respond with ONLY a JSON object:
{
  "name": "strategy-slug",
  "description": "...",
  "symbols": ["QQQ", "SPY"],
  "hold_days": 10,
  "warmup_bars": 60,
  "code": "<full Python source>"
}
Do NOT include markdown fences in the "code" field.
"""

_DANGEROUS_PATTERNS = [
    r"\bimport\s+(?!core\.indicators)",
    r"\bfrom\s+(?!core\.indicators)",
    r"\bopen\s*\(",
    r"\bsubprocess\b",
    r"\burllib\b",
    r"\brequests\b",
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"__import__",
]


def _check_safe(code: str) -> list[str]:
    """보안 패턴 검사. 위반 목록 반환 (빈 리스트 = 안전)."""
    issues = []
    # AST 구문 검사
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"SyntaxError: {e}"]

    for pattern in _DANGEROUS_PATTERNS:
        if re.search(pattern, code):
            issues.append(f"Dangerous pattern: {pattern}")

    # AST import 검사
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod != "core.indicators":
                    issues.append(f"Disallowed import: {mod}")
            else:
                for alias in node.names:
                    if alias.name != "core.indicators":
                        issues.append(f"Disallowed import: {alias.name}")

    return issues


def _dry_run(code: str) -> tuple[bool, str]:
    """생성된 코드를 가상 bars로 실행하여 기본 동작 검증."""
    # Minimal bar sequence
    bars = [
        {"ts": 1000 + i, "open": 100 + i * 0.1, "high": 101 + i * 0.1,
         "low": 99 + i * 0.1, "close": 100.5 + i * 0.1}
        for i in range(120)
    ]
    globs = {}
    root = Path(__file__).parents[1]
    sys.path.insert(0, str(root))
    try:
        exec(compile(code, "<strategy>", "exec"), globs)  # noqa: S102
    except Exception as e:
        return False, f"exec error: {e}"

    fn = globs.get("detect_signal")
    if not callable(fn):
        return False, "detect_signal function not found"

    try:
        sig = fn(bars[:30])  # below warmup → should return None
        if sig is not None:
            return False, "should return None when data < warmup_bars"
        sig = fn(bars)  # full data
    except Exception as e:
        return False, f"runtime error: {e}"

    if sig is not None:
        if "active" not in sig or "entry_price" not in sig or "stop_loss" not in sig:
            return False, "missing required keys in signal dict"
        if sig["active"] and sig.get("stop_loss", 0) >= sig.get("entry_price", 0):
            return False, "stop_loss must be < entry_price"

    return True, "ok"


def _call_anthropic(description: str, api_key: str, model: str) -> str:
    body = json.dumps({
        "model": model,
        "max_tokens": 2048,
        "system": _SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": description}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.load(r)
    return resp["content"][0]["text"]


def _call_openai(description: str, api_key: str, model: str) -> str:
    body = json.dumps({
        "model": model,
        "max_tokens": 2048,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": description},
        ],
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.load(r)
    return resp["choices"][0]["message"]["content"]


def generate(description: str) -> dict:
    """전략 설명 텍스트 → {name, description, code, path} dict.

    AI 백엔드 우선순위: Anthropic(claude-sonnet-4-20250514) → OpenAI(gpt-4o)
    생성된 코드는 보안 패턴 검사 + dry-run을 통과해야 한다.
    """
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")

    if anthropic_key:
        raw = _call_anthropic(description, anthropic_key, "claude-sonnet-4-20250514")
    elif openai_key:
        raw = _call_openai(description, openai_key, "gpt-4o")
    else:
        raise EnvironmentError("ANTHROPIC_API_KEY 또는 OPENAI_API_KEY가 설정되지 않았습니다")

    # JSON 파싱
    raw = raw.strip()
    if "```" in raw:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        raw = raw[start:end]
    data = json.loads(raw)

    code: str = data["code"]
    name: str = data.get("name", "custom-strategy")
    # slug 정리
    name = re.sub(r"[^a-z0-9\-]", "-", name.lower()).strip("-")

    # 보안 검사
    issues = _check_safe(code)
    if issues:
        raise ValueError(f"코드 보안 검사 실패:\n" + "\n".join(issues))

    # dry-run
    ok, msg = _dry_run(code)
    if not ok:
        raise ValueError(f"코드 검증 실패: {msg}")

    return {
        "name":        name,
        "description": data.get("description", description[:80]),
        "symbols":     data.get("symbols", ["QQQ", "SPY"]),
        "hold_days":   data.get("hold_days", 10),
        "warmup_bars": data.get("warmup_bars", 60),
        "code":        code,
    }


def save_strategy(result: dict, base_dir: str | Path = "skills/custom") -> Path:
    """생성된 전략을 skills/custom/<name>/strategy.py 에 저장."""
    base = Path(base_dir)
    skill_dir = base / result["name"]
    skill_dir.mkdir(parents=True, exist_ok=True)

    strategy_file = skill_dir / "strategy.py"
    strategy_file.write_text(result["code"], encoding="utf-8")

    # SKILL.md 자동 생성
    skill_md = textwrap.dedent(f"""\
        ---
        name: {result['name']}
        description: "{result['description']}"
        version: 1.0.0
        tags: [trading, strategy, generated]
        ---

        # {result['name']}

        > {result['description']}

        **심볼**: {', '.join(result['symbols'])}
        **보유 기간**: {result['hold_days']}일
        **워밍업**: {result['warmup_bars']}봉

        ## 백테스트

        ```bash
        python -m core.backtest_runner --skill {result['name']}
        ```

        ## 시뮬레이션

        텔레그램 `/sim {result['name']} start`
    """)
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

    return strategy_file
