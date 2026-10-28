"""行情图表服务: K 线 + AlphaTrend 指标

指标计算复用策略层 `alpha_trend_indicator`（单一实现）, API 不重写公式。
"""

from datetime import UTC, datetime

import pandas as pd
from pandas import DataFrame

from smart_trader.api.schemas import (
    CandleView,
    IndicatorPoint,
    MarketChartView,
    MarketDirection,
)
from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.exchange.ports import MarketDataPort
from smart_trader.model import Kline, Symbol, timeframe_seconds
from smart_trader.strategy.alpha_trend_signal.alpha_trend_indicator_v2 import (
    alpha_trend_indicator,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _frame(klines: list[Kline]) -> DataFrame:
    return DataFrame(
        [
            {
                "datetime": datetime.fromtimestamp(
                    kline.timestamp / 1000, UTC
                ).isoformat(),
                "open": kline.open,
                "high": kline.high,
                "low": kline.low,
                "close": kline.close,
                "volume": kline.volume,
            }
            for kline in klines
        ]
    )


def _direction(trend: int) -> MarketDirection:
    if trend > 0:
        return "long"
    if trend < 0:
        return "short"
    return "neutral"


def build_market_chart(
    market: MarketDataPort,
    symbol: Symbol,
    timeframe: str,
    limit: int,
) -> MarketChartView:
    """拉取 OHLCV 并计算指标; 无行情时抛 MARKET_DATA_UNAVAILABLE"""
    timeframe_seconds(timeframe)
    klines = market.fetch_ohlcv(symbol, timeframe, limit)
    if not klines:
        raise SmartTraderError(
            ErrorCode.MARKET_DATA_UNAVAILABLE,
            f"尚无行情: {symbol.ccxt()} {timeframe}",
        )

    enriched = alpha_trend_indicator(_frame(klines))
    indicator: list[IndicatorPoint] = []
    last_breakout: int | None = None
    for index, kline in enumerate(klines):
        alpha_raw = enriched["alpha_value"].iloc[index]
        trend_raw = enriched["trend"].iloc[index]
        breakout_raw = enriched["is_breakout"].iloc[index]
        is_breakout = False if pd.isna(breakout_raw) else bool(breakout_raw)
        if is_breakout:
            last_breakout = kline.timestamp
        indicator.append(
            IndicatorPoint(
                timestamp=kline.timestamp,
                alpha_value=(
                    None if pd.isna(alpha_raw) else float(alpha_raw)
                ),
                trend=0 if pd.isna(trend_raw) else int(trend_raw),
                is_breakout=is_breakout,
            )
        )

    trend = indicator[-1].trend
    return MarketChartView(
        symbol=symbol.ccxt(),
        timeframe=timeframe,
        fetched_at=_now(),
        candles=[
            CandleView(
                timestamp=kline.timestamp,
                open=kline.open,
                high=kline.high,
                low=kline.low,
                close=kline.close,
                volume=kline.volume,
                finished=kline.finished,
            )
            for kline in klines
        ],
        indicator=indicator,
        direction=_direction(trend),
        trend=trend,
        last_price=klines[-1].close,
        last_breakout_timestamp=last_breakout,
    )
