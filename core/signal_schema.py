"""Standard signal schema shared across all trading skills."""
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Signal:
    symbol: str
    exchange: str        # 'binance_futures' | 'binance_spot' | 'upbit_spot' | 'kiwoom'
    timeframe: str       # '1h' | '4h' | '1d'
    score: float
    direction: str       # 'long' | 'hold'
    entry: float
    regime: str          # 'risk_on' | 'risk_off' | 'neutral'
    source: str          # 'crypto-recommender-v3' | 'holy-grail' | 'second-play'
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    stop: Optional[float] = None
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d['ts'] = self.ts.isoformat()
        return d
