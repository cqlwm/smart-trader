"""停机信号补偿集成测试: 重启后按历史信号价补挂限价单

场景: 进程停机期间出现反向信号 → 重启 → 首根完成K线回放缺口 →
对最早一次离场信号按该 K 线收盘价挂限价单（不追价）。
"""

from pathlib import Path

import pytest
from pandas import DataFrame

from smart_trader.model import (
    Kline,
    MarketSignal,
    OrderSide,
    PlaceOrderBehavior,
    SignalDirection,
    SignalKind,
)
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.strategy.signal import Signal
from smart_trader.strategy.signal_grid_config import SignalGridStrategyConfig
from smart_trader.strategy.signal_grid_strategy import SignalGridStrategy
from tests.fake_exchange import BookEntry, StatefulFakeExchange
from tests.fakes import FakeMarketDataPort, make_kline
from tests.integration_harness import (
    DEFAULT_STRATEGY_KEY,
    KlineDriver,
    make_config,
    read_orders,
)

START = 1_700_000_000_000
MINUTE = 60_000


class _BandSignal(Signal):
    """确定性信号: 高位看多（用于建仓）, 低位看空（用于离场）, 中间中性

    无内部去重状态, 重复回放同一根K线得到相同结果。
    """

    def __init__(self, long_at: float = 0.5, short_below: float = 0.485) -> None:
        self._long_at = long_at
        self._short_below = short_below

    def evaluate(self, klines: DataFrame) -> MarketSignal:
        close = float(klines["close"].iloc[-1])
        if close >= self._long_at:
            direction = SignalDirection.LONG
        elif close < self._short_below:
            direction = SignalDirection.SHORT
        else:
            direction = SignalDirection.NEUTRAL
        return MarketSignal(direction=direction)


def _config(**overrides: object) -> SignalGridStrategyConfig:
    fields: dict[str, object] = {
        "fixed_rate_take_profit": False,
        "enable_signal_take_profit": False,
        "enable_signal_stop_loss": True,
        "enable_single_layer_signal_stop_loss": True,
        "signal_kind": SignalKind.DIRECTIONAL,
        "signal": _BandSignal(),
        "place_order_behavior": PlaceOrderBehavior.NORMAL,
    }
    fields.update(overrides)
    return make_config(**fields)


def _open_long(
    ledger: str,
) -> tuple[StatefulFakeExchange, OrderJournal, SignalGridStrategy]:
    """走真实入场路径建立一个多头仓位, 并把入场单成交"""
    exchange = StatefulFakeExchange()
    journal = OrderJournal(ledger)
    strategy = SignalGridStrategy(
        _config(),
        exchange,
        FakeMarketDataPort(),
        journal,
        exchange,
        reconcile_min_age_seconds=0.0,
    )
    driver = KlineDriver(strategy)
    driver.push(0.5)

    entry_id = next(
        key for key, entry in exchange.book.items() if entry.side is OrderSide.BUY
    )
    exchange.fill(entry_id, 100.0, avg_price=0.5)
    driver.push(0.5)  # 结算入场单

    assert strategy.positions[0].quantity == pytest.approx(100.0)
    return exchange, journal, strategy


def _restart(
    strategy: SignalGridStrategy,
    exchange: StatefulFakeExchange,
    journal: OrderJournal,
    klines: list[Kline],
) -> SignalGridStrategy:
    """模拟重启: 新信号实例 + 带缺口历史K线的行情源"""
    return SignalGridStrategy(
        _config(),
        exchange,
        FakeMarketDataPort(klines=klines),
        journal,
        exchange,
        reconcile_min_age_seconds=0.0,
    )


def _push(strategy: SignalGridStrategy, close: float, timestamp: int) -> None:
    strategy.run(
        make_kline(
            symbol=strategy.config.symbol,
            timeframe=strategy.config.timeframe,
            close=close,
            timestamp=timestamp,
        )
    )


def _exit_orders(exchange: StatefulFakeExchange) -> list[BookEntry]:
    return [
        entry for entry in exchange.book.values() if entry.side is OrderSide.SELL
    ]


def _gap_klines(watermark: int) -> list[Kline]:
    """两根缺口K线: 0.49 中性, 0.48 触发反向; 之后是恢复后的实时K线"""
    return [
        make_kline(
            symbol=make_config().symbol,
            timestamp=watermark - MINUTE,
            close=0.5,
        ),
        make_kline(
            symbol=make_config().symbol,
            timestamp=watermark,
            close=0.5,
        ),
        make_kline(
            symbol=make_config().symbol,
            timestamp=watermark + MINUTE,
            close=0.49,
        ),
        make_kline(
            symbol=make_config().symbol,
            timestamp=watermark + 2 * MINUTE,
            close=0.48,
        ),
    ]


def test_restart_places_limit_order_at_missed_signal_price(tmp_path: Path) -> None:
    ledger = str(tmp_path / "smart_trader.db")
    exchange, journal, strategy = _open_long(ledger)
    watermark = journal.get_kline_watermark(DEFAULT_STRATEGY_KEY)
    assert watermark is not None, "完成K线必须记录水位"

    klines = _gap_klines(watermark)
    restarted = _restart(strategy, exchange, journal, klines)
    _push(restarted, close=0.49, timestamp=watermark + 3 * MINUTE)

    exits = _exit_orders(exchange)
    assert len(exits) == 1, "停机期间的反向信号必须补挂一张出场单"
    assert exits[0].price == pytest.approx(0.48), (
        "补偿单必须使用信号K线收盘价, 而不是恢复后的实时价 0.49"
    )
    assert exits[0].quantity == pytest.approx(100.0)

    rows = read_orders(ledger, DEFAULT_STRATEGY_KEY)
    exit_rows = [row for row in rows if row["side"] == "sell"]
    assert len(exit_rows) == 1, "补偿单必须落账"
    assert journal.get_kline_watermark(DEFAULT_STRATEGY_KEY) == (
        watermark + 3 * MINUTE
    ), "补偿完成后水位应推进到最新处理K线"


def test_compensation_is_not_repeated_on_later_klines(tmp_path: Path) -> None:
    ledger = str(tmp_path / "smart_trader.db")
    exchange, journal, strategy = _open_long(ledger)
    watermark = journal.get_kline_watermark(DEFAULT_STRATEGY_KEY)
    assert watermark is not None

    klines = _gap_klines(watermark)
    restarted = _restart(strategy, exchange, journal, klines)
    _push(restarted, close=0.49, timestamp=watermark + 3 * MINUTE)
    _push(restarted, close=0.49, timestamp=watermark + 4 * MINUTE)

    assert len(_exit_orders(exchange)) == 1, (
        "补偿只在本进程首根完成K线尝试一次, 后续K线不得重复补单"
    )


def test_compensation_retries_next_kline_when_place_fails(
    tmp_path: Path,
) -> None:
    ledger = str(tmp_path / "smart_trader.db")
    exchange, journal, strategy = _open_long(ledger)
    watermark = journal.get_kline_watermark(DEFAULT_STRATEGY_KEY)
    assert watermark is not None

    klines = _gap_klines(watermark)
    restarted = _restart(strategy, exchange, journal, klines)
    exchange.drop_request_next = True

    _push(restarted, close=0.49, timestamp=watermark + 3 * MINUTE)
    assert _exit_orders(exchange) == [], "下单失败时本轮不应留下订单"
    assert journal.get_kline_watermark(DEFAULT_STRATEGY_KEY) == watermark, (
        "补偿失败时水位不得前进, 否则信号永久丢失"
    )

    _push(restarted, close=0.49, timestamp=watermark + 4 * MINUTE)
    exits = _exit_orders(exchange)
    assert len(exits) == 1, "下一根完成K线必须重试补偿"
    assert exits[0].price == pytest.approx(0.48)
