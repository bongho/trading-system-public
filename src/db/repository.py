from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from src.brokers.base import MarketData

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


def _now_kst() -> str:
    return datetime.now(KST).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class TradeRepository:
    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def insert_trade(
        self,
        *,
        strategy_id: str,
        broker: str,
        side: str,
        symbol: str,
        amount: float,
        price: float,
        volume: float,
        fee: float = 0,
        pnl: float | None = None,
        pnl_pct: float | None = None,
    ) -> str:
        trade_id = _new_id()
        await self._db.execute(
            """INSERT INTO trades
            (id, strategy_id, broker, side, symbol, amount, price, volume,
             fee, pnl, pnl_pct, executed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade_id,
                strategy_id,
                broker,
                side,
                symbol,
                amount,
                price,
                volume,
                fee,
                pnl,
                pnl_pct,
                _now_kst(),
            ),
        )
        await self._db.commit()
        return trade_id

    async def get_recent_trades(
        self, strategy_id: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        if strategy_id:
            cursor = await self._db.execute(
                "SELECT * FROM trades WHERE strategy_id = ? ORDER BY executed_at DESC LIMIT ?",
                (strategy_id, limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM trades ORDER BY executed_at DESC LIMIT ?",
                (limit,),
            )
        columns = [desc[0] for desc in cursor.description]
        rows = await cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]

    async def get_strategy_stats(self, strategy_id: str) -> dict[str, Any]:
        cursor = await self._db.execute(
            """SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as win_count,
                SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as loss_count,
                AVG(CASE WHEN pnl > 0 THEN pnl END) as avg_profit,
                AVG(CASE WHEN pnl <= 0 THEN pnl END) as avg_loss,
                MAX(pnl) as max_profit,
                MIN(pnl) as max_loss,
                SUM(pnl) as total_pnl
            FROM trades
            WHERE strategy_id = ? AND side = 'sell' AND pnl IS NOT NULL""",
            (strategy_id,),
        )
        row = await cursor.fetchone()
        columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, row)) if row else {}

    async def get_pnl_by_period(self, period: str = "today") -> dict[str, Any]:
        """기간별 손익 조회. period: today, week, month, all"""
        now = datetime.now(KST)
        if period == "today":
            since = now.replace(hour=0, minute=0, second=0).isoformat()
        elif period == "week":
            since = (now - timedelta(days=7)).isoformat()
        elif period == "month":
            since = (now - timedelta(days=30)).isoformat()
        else:
            since = "2000-01-01"

        cursor = await self._db.execute(
            """SELECT
                strategy_id,
                COUNT(*) as trades,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses,
                COALESCE(SUM(pnl), 0) as total_pnl,
                COALESCE(SUM(fee), 0) as total_fee
            FROM trades
            WHERE side = 'sell' AND pnl IS NOT NULL
                AND executed_at >= ?
            GROUP BY strategy_id""",
            (since,),
        )
        columns = [desc[0] for desc in cursor.description]
        rows = await cursor.fetchall()
        return {
            "period": period,
            "since": since[:10],
            "strategies": [dict(zip(columns, row)) for row in rows],
        }


class StrategyRepository:
    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def upsert_strategy(
        self,
        *,
        id: str,
        name: str,
        broker: str,
        symbols: list[str],
        capital_allocation: float,
        current_capital: float | None = None,
        interval_minutes: int = 5,
        enabled: bool = True,
        params: dict[str, Any] | None = None,
        code_path: str = "",
    ) -> None:
        await self._db.execute(
            """INSERT INTO strategies
            (id, name, broker, symbols, capital_allocation, current_capital,
             interval_minutes, enabled, params, code_path, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, broker=excluded.broker,
                symbols=excluded.symbols,
                capital_allocation=excluded.capital_allocation,
                current_capital=excluded.current_capital,
                interval_minutes=excluded.interval_minutes,
                enabled=excluded.enabled,
                params=excluded.params,
                code_path=excluded.code_path,
                updated_at=excluded.updated_at""",
            (
                id,
                name,
                broker,
                json.dumps(symbols),
                capital_allocation,
                current_capital or capital_allocation,
                interval_minutes,
                int(enabled),
                json.dumps(params or {}),
                code_path,
                _now_kst(),
            ),
        )
        await self._db.commit()

    async def get_strategy(self, strategy_id: str) -> dict[str, Any] | None:
        cursor = await self._db.execute(
            "SELECT * FROM strategies WHERE id = ?",
            (strategy_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        columns = [desc[0] for desc in cursor.description]
        result = dict(zip(columns, row))
        result["symbols"] = json.loads(result["symbols"])
        result["params"] = json.loads(result["params"])
        result["enabled"] = bool(result["enabled"])
        return result

    async def get_all_strategies(self) -> list[dict[str, Any]]:
        cursor = await self._db.execute("SELECT * FROM strategies")
        columns = [desc[0] for desc in cursor.description]
        rows = await cursor.fetchall()
        results = []
        for row in rows:
            d = dict(zip(columns, row))
            d["symbols"] = json.loads(d["symbols"])
            d["params"] = json.loads(d["params"])
            d["enabled"] = bool(d["enabled"])
            results.append(d)
        return results

    async def update_capital(self, strategy_id: str, capital: float) -> None:
        await self._db.execute(
            "UPDATE strategies SET current_capital = ?, updated_at = ? WHERE id = ?",
            (capital, _now_kst(), strategy_id),
        )
        await self._db.commit()

    async def set_enabled(self, strategy_id: str, enabled: bool) -> None:
        await self._db.execute(
            "UPDATE strategies SET enabled = ?, updated_at = ? WHERE id = ?",
            (int(enabled), _now_kst(), strategy_id),
        )
        await self._db.commit()

    async def update_params(self, strategy_id: str, params: dict[str, Any]) -> None:
        await self._db.execute(
            "UPDATE strategies SET params = ?, updated_at = ? WHERE id = ?",
            (json.dumps(params), _now_kst(), strategy_id),
        )
        await self._db.commit()


class PendingTradeRepository:
    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def create_pending(
        self, command: dict[str, Any], ttl_seconds: int = 300
    ) -> str:
        pending_id = _new_id()
        expires_at = (datetime.now(KST) + timedelta(seconds=ttl_seconds)).isoformat()
        await self._db.execute(
            "INSERT INTO pending_trades (id, command, expires_at) VALUES (?, ?, ?)",
            (pending_id, json.dumps(command), expires_at),
        )
        await self._db.commit()
        return pending_id

    async def get_pending(self, pending_id: str) -> dict[str, Any] | None:
        cursor = await self._db.execute(
            "SELECT * FROM pending_trades WHERE id = ? AND expires_at > ?",
            (pending_id, _now_kst()),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        columns = [desc[0] for desc in cursor.description]
        result = dict(zip(columns, row))
        result["command"] = json.loads(result["command"])
        return result

    async def delete_pending(self, pending_id: str) -> None:
        await self._db.execute("DELETE FROM pending_trades WHERE id = ?", (pending_id,))
        await self._db.commit()

    async def cleanup_expired(self) -> int:
        cursor = await self._db.execute(
            "DELETE FROM pending_trades WHERE expires_at <= ?",
            (_now_kst(),),
        )
        await self._db.commit()
        return cursor.rowcount


class MarketDataRepository:
    """OHLCV 캔들 데이터 저장/조회 — Read-through 캐시 패턴."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def upsert_candles(
        self, candles: list[MarketData], timeframe: str = "5m"
    ) -> int:
        """캔들 데이터 일괄 저장 (중복 시 업데이트)."""
        if not candles:
            return 0
        await self._db.executemany(
            """INSERT INTO market_data
            (symbol, timeframe, timestamp, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, timeframe, timestamp) DO UPDATE SET
                open=excluded.open, high=excluded.high,
                low=excluded.low, close=excluded.close,
                volume=excluded.volume""",
            [
                (
                    c.symbol, timeframe, c.timestamp,
                    c.open, c.high, c.low, c.close, c.volume,
                )
                for c in candles
            ],
        )
        await self._db.commit()
        return len(candles)

    async def get_candles(
        self,
        symbol: str,
        timeframe: str = "5m",
        *,
        since: str | None = None,
        until: str | None = None,
        limit: int | None = None,
    ) -> list[MarketData]:
        """저장된 캔들 조회 (시간순 정렬)."""
        query = (
            "SELECT symbol, timestamp, open, high, low, close, volume "
            "FROM market_data WHERE symbol = ? AND timeframe = ?"
        )
        params: list[Any] = [symbol, timeframe]

        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        if until:
            query += " AND timestamp <= ?"
            params.append(until)

        query += " ORDER BY timestamp ASC"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()
        return [
            MarketData(
                symbol=row[0],
                timestamp=row[1],
                open=row[2],
                high=row[3],
                low=row[4],
                close=row[5],
                volume=row[6],
            )
            for row in rows
        ]

    async def get_candle_count(
        self, symbol: str, timeframe: str = "5m"
    ) -> int:
        """저장된 캔들 수 조회."""
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM market_data WHERE symbol = ? AND timeframe = ?",
            (symbol, timeframe),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def get_latest_timestamp(
        self, symbol: str, timeframe: str = "5m"
    ) -> str | None:
        """가장 최근 캔들 타임스탬프 조회."""
        cursor = await self._db.execute(
            "SELECT MAX(timestamp) FROM market_data "
            "WHERE symbol = ? AND timeframe = ?",
            (symbol, timeframe),
        )
        row = await cursor.fetchone()
        return row[0] if row and row[0] else None

    async def get_oldest_timestamp(
        self, symbol: str, timeframe: str = "5m"
    ) -> str | None:
        """가장 오래된 캔들 타임스탬프 조회."""
        cursor = await self._db.execute(
            "SELECT MIN(timestamp) FROM market_data "
            "WHERE symbol = ? AND timeframe = ?",
            (symbol, timeframe),
        )
        row = await cursor.fetchone()
        return row[0] if row and row[0] else None

    async def cleanup_old(self, days: int = 365) -> int:
        """오래된 데이터 정리."""
        cutoff = (datetime.now(KST) - timedelta(days=days)).isoformat()
        cursor = await self._db.execute(
            "DELETE FROM market_data WHERE timestamp < ?",
            (cutoff,),
        )
        await self._db.commit()
        return cursor.rowcount
