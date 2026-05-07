"""
Rule-based Bull/Bear Debate filter.

Inspired by vibe-investing's Bull/Bear/Judge pattern but implemented
without LLM calls — pure signal metrics.

A signal is blocked when both bullish and bearish evidence are simultaneously
strong (conflicting market conditions). This prevents entering trades where
the edge is unclear.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DebateResult:
    passed: bool
    bull_score: int    # 0-4
    bear_score: int    # 0-4
    verdict: str       # 'clear_bull' | 'clear_bear' | 'conflicting' | 'weak'
    reason: str

    def to_dict(self) -> dict:
        return {
            'passed':     self.passed,
            'bull_score': self.bull_score,
            'bear_score': self.bear_score,
            'verdict':    self.verdict,
            'reason':     self.reason,
        }


def debate(
    ch_1h: float,
    rsi: float,
    vol_ratio: float,
    ch_15m: float = 0.0,
    regime: str = 'neutral',
) -> DebateResult:
    """Evaluate signal through Bull vs Bear debate.

    Bullish evidence:
      +1  strong 1h momentum (ch_1h > 1.5%)
      +1  volume surge (vol_ratio > 2.5×)
      +1  RSI not overbought and not oversold (45 < rsi < 58)
      +1  positive short-term momentum (ch_15m > 0.3%)

    Bearish evidence:
      +1  overbought (rsi > 65)
      +1  negative 1h (ch_1h < 0)
      +1  low volume surge (vol_ratio < 1.2) — no conviction
      +1  risk-off regime

    Block when both sides score ≥ 2 simultaneously (conflicting evidence).
    """
    bull_checks = [
        ch_1h > 1.5,
        vol_ratio > 2.5,
        45.0 < rsi < 58.0,
        ch_15m > 0.3,
    ]
    bear_checks = [
        rsi > 65.0,
        ch_1h < 0.0,
        vol_ratio < 1.2,
        regime == 'risk_off',
    ]

    bull_score = sum(bull_checks)
    bear_score = sum(bear_checks)

    if bull_score >= 2 and bear_score >= 2:
        verdict = 'conflicting'
        passed  = False
        reason  = f"bull={bull_score}/4 bear={bear_score}/4 — conflicting signals, blocked"
    elif bull_score == 0 and bear_score == 0:
        verdict = 'weak'
        passed  = False
        reason  = f"bull={bull_score}/4 bear={bear_score}/4 — no conviction on either side"
    elif bull_score >= bear_score:
        verdict = 'clear_bull'
        passed  = True
        reason  = f"bull={bull_score}/4 bear={bear_score}/4 — bullish edge"
    else:
        verdict = 'clear_bear'
        passed  = False
        reason  = f"bull={bull_score}/4 bear={bear_score}/4 — bearish dominates, blocked"

    return DebateResult(
        passed=passed,
        bull_score=bull_score,
        bear_score=bear_score,
        verdict=verdict,
        reason=reason,
    )
