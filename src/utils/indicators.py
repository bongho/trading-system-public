"""기술 지표 계산 모듈.

pandas-ta를 사용하되, 핵심 지표는 직접 구현하여 의존성 최소화.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.brokers.base import MarketData


def to_dataframe(data: list[MarketData]) -> pd.DataFrame:
    """MarketData 리스트를 DataFrame으로 변환"""
    df = pd.DataFrame(
        [
            {
                "timestamp": d.timestamp,
                "open": d.open,
                "high": d.high,
                "low": d.low,
                "close": d.close,
                "volume": d.volume,
            }
            for d in data
        ]
    )
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df.set_index("timestamp", inplace=True)
    return df


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def bollinger_bands(
    series: pd.Series, period: int = 20, std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """볼린저 밴드 (upper, middle, lower)"""
    middle = sma(series, period)
    std_dev = series.rolling(window=period).std()
    upper = middle + std * std_dev
    lower = middle - std * std_dev
    return upper, middle, lower


def atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    """Average True Range"""
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def keltner_channel(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 20,
    atr_mult: float = 1.5,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Keltner Channel (upper, middle, lower)"""
    middle = ema(close, period)
    atr_val = atr(high, low, close, period)
    upper = middle + atr_mult * atr_val
    lower = middle - atr_mult * atr_val
    return upper, middle, lower


def squeeze_momentum(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    bb_period: int = 20,
    bb_std: float = 2.0,
    kc_period: int = 20,
    kc_atr_mult: float = 1.5,
    mom_period: int = 20,
) -> tuple[pd.Series, pd.Series]:
    """Squeeze Momentum Indicator.

    Returns:
        squeeze_on: True when BB is inside KC (squeeze active)
        momentum: Linear regression of price deviation
    """
    bb_upper, bb_mid, bb_lower = bollinger_bands(close, bb_period, bb_std)
    kc_upper, kc_mid, kc_lower = keltner_channel(
        high, low, close, kc_period, kc_atr_mult
    )

    # Squeeze state
    squeeze_on = (bb_lower > kc_lower) & (bb_upper < kc_upper)

    # Momentum: linear regression of (close - avg(H,L,C,C)/4) over mom_period
    avg_price = (high + low + close + close) / 4
    deviation = close - sma(avg_price, mom_period)

    # Simple linear regression value (using rolling)
    momentum = _linear_regression_value(deviation, mom_period)

    return squeeze_on, momentum


def _linear_regression_value(series: pd.Series, period: int) -> pd.Series:
    """Rolling linear regression - returns the current fitted value."""

    def _lr(window: np.ndarray) -> float:
        if len(window) < period or np.isnan(window).any():
            return np.nan
        x = np.arange(len(window))
        coeffs = np.polyfit(x, window, 1)
        return np.polyval(coeffs, len(window) - 1)

    return series.rolling(window=period).apply(_lr, raw=True)
