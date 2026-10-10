"""AlphaTrendSignal 真实行情冒烟测试（integration）

用真实 ccxt K 线验证 `evaluate` 在真实数据上：
- 不抛异常、方向取值合法；
- 同一根 K 线重复求值结果一致（幂等）;
- 状态（`current_signal` / `trend`）随行情推进而更新。

**不做买卖方向映射验证** —— 那是 `EntryGate` / `ExitPolicy` 的职责，
已由 `tests/test_entry_gate.py` 与 `tests/test_exit_policy.py` 覆盖。

默认不运行（`pyproject.toml` 的 `addopts = ["-m", "not integration"]`），
需要时显式执行: `uv run pytest -m integration tests/test_alpha_trend_signal.py`
"""

import sys
from datetime import UTC, datetime
from typing import Any

import ccxt  # pyright: ignore[reportMissingTypeStubs]
import pandas as pd
import pytest

from smart_trader.model import SignalDirection
from smart_trader.strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal


def fetch_klines_from_ccxt(
    symbol: str = "BTC/USDT", timeframe: str = "1m", limit: int = 1000
) -> pd.DataFrame:
    """使用 ccxt 获取真实 K 线数据（无需 API 密钥）"""
    exchange: Any = ccxt.binance({"options": {"defaultType": "future"}})
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)

    rows: list[dict[str, Any]] = []
    for candle in ohlcv:
        timestamp, open_price, high_price, low_price, close_price, volume = candle
        rows.append(
            {
                "datetime": datetime.fromtimestamp(timestamp / 1000, tz=UTC)
                .astimezone()
                .strftime("%Y-%m-%d %H:%M:%S"),
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
                "finished": True,
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def klines() -> pd.DataFrame:
    """真实 BTC/USDT 1 分钟 K 线（整个模块共用, 只拉一次）"""
    return fetch_klines_from_ccxt()


@pytest.mark.integration
def test_evaluate_on_real_klines_produces_valid_direction(klines: pd.DataFrame):
    """真实行情上求值不抛异常, 方向取值合法"""
    signal = AlphaTrendSignal()

    result = signal.evaluate(klines)

    assert result.direction in tuple(SignalDirection), (
        f"方向必须是合法枚举, 实际得到 {result.direction}"
    )
    assert result.datetime != "", "真实行情求值后应携带 K 线时间"


@pytest.mark.integration
def test_evaluate_is_idempotent_on_real_klines(klines: pd.DataFrame):
    """真实行情上同一根 K 线重复求值结果一致（幂等）"""
    signal = AlphaTrendSignal()

    first = signal.evaluate(klines)
    second = signal.evaluate(klines)

    assert first.direction is second.direction, "同一根 K 线重复求值方向必须一致"
    assert first.datetime == second.datetime


@pytest.mark.integration
def test_evaluate_advances_state_across_klines(klines: pd.DataFrame):
    """行情推进时信号状态随之更新（趋势计数非零, 内部时间落在最后一根）"""
    signal = AlphaTrendSignal()
    signal.evaluate(klines.iloc[:-1])
    signal.evaluate(klines)
    latest = signal.evaluate(klines)

    assert signal.datetime == str(klines["datetime"].iloc[-1]), (
        "内部时间应停在最后一根 K 线"
    )
    assert signal.current_trend != 0, (
        f"真实行情下趋势计数应非零, 实际 {signal.current_trend}"
    )
    assert latest.direction in tuple(SignalDirection)


@pytest.mark.integration
def test_evaluate_emits_signals_on_real_trend_reversals(klines: pd.DataFrame):
    """真实行情推进过程中至少产出一次信号（否则说明指标未工作）"""
    signal = AlphaTrendSignal()
    emitted = 0

    for index in range(30, len(klines)):
        window = klines.iloc[: index + 1]
        if signal.evaluate(window).direction is not SignalDirection.NEUTRAL:
            emitted += 1

    assert emitted > 0, (
        f"真实行情 1 分钟 K 线推进中应至少出现一次突破信号, 实际 {emitted} 次"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-m", "integration", "-v"]))