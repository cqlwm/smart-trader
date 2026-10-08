
import pytest

from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.model import (
    Kline,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    PlaceOrderBehavior,
    PositionSide,
    Symbol,
)
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.strategy.signal_grid_strategy import (
    GridPosition,
    SignalGridStrategy,
    SignalGridStrategyConfig,
)
from tests.fakes import (
    FakeMarketDataPort,
    FakeTradingPort,
    make_entry_intent,
)

TIMEFRAME = "1m"
SYMBOL = Symbol(base="BTC", quote="USDT")
STRATEGY_KEY = "btcusdt_test_1m"


def set_latest_kline(strategy: SignalGridStrategy, close: float) -> None:
    strategy.kline_data_dict[TIMEFRAME].latest_kline = Kline(
        symbol=strategy.config.symbol,
        timeframe=TIMEFRAME,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=0.0,
        timestamp=0,
        finished=True,
    )


def set_latest_kline_range(
    strategy: SignalGridStrategy, close: float, high: float, low: float
) -> None:
    strategy.kline_data_dict[TIMEFRAME].latest_kline = Kline(
        symbol=strategy.config.symbol,
        timeframe=TIMEFRAME,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=0.0,
        timestamp=0,
        finished=True,
    )


def build_position(
    entry_id: str,
    price: float,
    quantity: float,
    side: OrderSide = OrderSide.BUY,
    filled_quantity: float | None = None,
    status: OrderStatus = OrderStatus.CLOSED,
) -> GridPosition:
    return GridPosition(
        entry_id=entry_id,
        side=side,
        price=price,
        quantity=quantity,
        filled_quantity=quantity if filled_quantity is None else filled_quantity,
        fixed_take_profit_rate=0.01,
        signal_min_take_profit_rate=0.002,
        status=status,
    )


def build_config(**overrides: object) -> SignalGridStrategyConfig:
    fields: dict[str, object] = {
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "per_order_qty": 100,
        "strategy_key": STRATEGY_KEY,
        "fixed_rate_take_profit": True,
        "fixed_take_profit_rate": 0.01,
        "place_order_behavior": PlaceOrderBehavior.NORMAL,
    }
    fields.update(overrides)
    return SignalGridStrategyConfig.model_validate(fields)


def build_strategy(
    config: SignalGridStrategyConfig | None = None,
    journal: OrderJournal | None = None,
) -> tuple[SignalGridStrategy, FakeTradingPort, FakeMarketDataPort]:
    trading = FakeTradingPort(status=OrderStatus.OPEN, price=0.0)
    market = FakeMarketDataPort()
    return (
        SignalGridStrategy(
            config or build_config(),
            trading,
            market,
            journal if journal is not None else OrderJournal(":memory:"),
            reconcile_min_age_seconds=0.0,
        ),
        trading,
        market,
    )


class TestCloseFullQuantity:
    """平仓恒定按实际成交量全额执行 (平仓比例参数已移除)"""

    def test_config_no_longer_exposes_close_position_ratio(self):
        """平仓比例参数必须已从配置中移除"""
        assert "close_position_ratio" not in SignalGridStrategyConfig.model_fields

    def test_closes_full_quantity(self):
        strategy, trading, _ = build_strategy()
        strategy.position_manager.add_position(build_position("test1", 100.0, 100.0))
        set_latest_kline(strategy, 101.1)

        closed_positions = strategy.check_close_position()

        assert len(closed_positions) == 1
        assert trading.placed[0].quantity == pytest.approx(100.0)

    def test_multiple_orders_are_summed(self):
        strategy, trading, _ = build_strategy()
        strategy.position_manager.add_position(build_position("test1", 100.0, 100.0))
        strategy.position_manager.add_position(build_position("test2", 99.0, 50.0))
        set_latest_kline(strategy, 101.1)

        closed_positions = strategy.check_close_position()

        assert len(closed_positions) == 2
        # 总数量 = 100 + 50 = 150
        assert trading.placed[0].quantity == pytest.approx(150.0)

    def test_exit_order_is_reversal_side(self):
        strategy, trading, _ = build_strategy()
        strategy.position_manager.add_position(build_position("test1", 100.0, 100.0))
        set_latest_kline(strategy, 101.1)

        strategy.check_close_position()

        request = trading.placed[0]
        assert request.side is OrderSide.SELL, "买单的平仓单必须是卖单"
        assert request.type is OrderType.LIMIT
        assert request.position_side is PositionSide.LONG


class TestExitQuantityUsesFilledAmount:
    """平仓数量必须按实际成交量计算, 否则部分成交时会超卖"""

    def test_partial_fill_closes_only_filled_quantity(self):
        strategy, trading, _ = build_strategy()
        strategy.position_manager.add_position(
            build_position("test1", 100.0, 100.0, filled_quantity=30.0)
        )
        set_latest_kline(strategy, 101.1)

        strategy.check_close_position()

        assert trading.placed[0].quantity == pytest.approx(30.0), (
            "只有 30 成交, 平仓就只能是 30"
        )

    def test_partially_filled_orders_are_summed_by_filled_amount(self):
        strategy, trading, _ = build_strategy()
        strategy.position_manager.add_position(
            build_position("t1", 100.0, 100.0, filled_quantity=30.0)
        )
        strategy.position_manager.add_position(
            build_position("t2", 99.0, 50.0, filled_quantity=50.0)
        )
        set_latest_kline(strategy, 101.1)

        strategy.check_close_position()

        assert trading.placed[0].quantity == pytest.approx(80.0)

    def test_fully_filled_order_closes_full_quantity(self):
        strategy, trading, _ = build_strategy()
        strategy.position_manager.add_position(
            build_position("t1", 100.0, 100.0, filled_quantity=100.0)
        )
        set_latest_kline(strategy, 101.1)

        strategy.check_close_position()

        assert trading.placed[0].quantity == pytest.approx(100.0)

    def test_unfilled_order_does_not_place_exit(self):
        """入场单还没成交就不该平仓"""
        strategy, trading, _ = build_strategy()
        strategy.position_manager.add_position(
            build_position(
                "t1",
                100.0,
                100.0,
                filled_quantity=0.0,
                status=OrderStatus.CANCELED,
            )
        )
        set_latest_kline(strategy, 101.1)

        strategy.check_close_position()

        assert trading.placed == [], "未成交的订单不应触发平仓"

    def test_real_time_take_profit_uses_filled_quantity(self):
        config = build_config(take_profit_use_limit_order=True)
        strategy, trading, _ = build_strategy(config)
        strategy.position_manager.add_position(
            build_position(
                "entry1",
                100.0,
                100.0,
                filled_quantity=40.0,
                status=OrderStatus.CLOSED,
            )
        )
        set_latest_kline(strategy, 100.0)

        strategy._on_kline()

        assert trading.placed[0].quantity == pytest.approx(40.0), (
            "止盈数量 = 实际成交量"
        )


class TestOpenOrder:
    def test_records_exchange_side_effects(self):
        """入场订单号与成交价必须以交易所返回为准"""
        strategy, trading, _ = build_strategy()
        trading.queue_place(
            OrderResult(
                custom_id="rewritten_cid",
                symbol=SYMBOL,
                side=OrderSide.BUY,
                status=OrderStatus.OPEN,
                quantity=100.0,
                price=100.0,
            )
        )
        set_latest_kline(strategy, 100.0)

        assert strategy.check_open_position() is True

        position = strategy.position_manager.positions[0]
        assert position.entry_id == "rewritten_cid"
        assert position.price == pytest.approx(100.0)
        assert position.status is OrderStatus.OPEN

    def test_does_not_open_when_price_outside_grid_range(self):
        strategy, trading, _ = build_strategy(build_config(lowest_price=200.0))
        set_latest_kline(strategy, 100.0)

        assert strategy.check_open_position() is False
        assert trading.placed == []

    def test_does_not_open_when_max_order_reached(self):
        strategy, trading, _ = build_strategy(build_config(max_order=1))
        strategy.position_manager.add_position(build_position("existing", 100.0, 100.0))
        set_latest_kline(strategy, 100.0)

        assert strategy.check_open_position() is False
        assert trading.placed == []


class TestChaserBehavior:
    def test_chaser_open_is_used_when_configured(self):
        """追单行为下必须以 GTX 只做 maker 的方式挂单"""
        strategy, trading, _ = build_strategy(
            build_config(place_order_behavior=PlaceOrderBehavior.CHASER_OPEN)
        )
        set_latest_kline(strategy, 100.0)

        assert strategy.check_open_position() is True

        request = trading.placed[0]
        assert request.time_in_force is not None, "追单必须使用 GTX"
        assert request.time_in_force.value == "GTX"

    def test_falls_back_to_normal_order_when_chaser_gives_up(self):
        """追单递进耗尽仍未挂上时, 必须降级为常规限价单, 不能静默丢单"""
        strategy, trading, _ = build_strategy(
            build_config(place_order_behavior=PlaceOrderBehavior.CHASER_OPEN)
        )
        rejections = [
            SmartTraderError(ErrorCode.ORDER_WOULD_TAKE_LIQUIDITY, "会被立即成交")
        ] * 10
        trading.queue_place(*rejections)
        set_latest_kline(strategy, 100.0)

        assert strategy.check_open_position() is True

        assert len(trading.placed) == 11, "10 次追单失败后应有 1 次常规兜底"
        assert trading.placed[-1].time_in_force is None, "兜底单是常规限价单"


class TestOrderStatusHandling:
    def test_missing_entry_order_is_treated_as_expired(self):
        """交易所查不到入场单: 做撤单处理而不是继续等待"""
        strategy, trading, _ = build_strategy()
        position = build_position("gone", 100.0, 100.0)
        position.status = OrderStatus.OPEN
        strategy.position_manager.add_position(position)
        trading.queue_query(None)
        set_latest_kline(strategy, 101.1)

        closed_positions = strategy.check_close_position()

        assert closed_positions == [position]
        assert trading.canceled == [], "订单已不存在, 无需撤单"

    def test_open_entry_order_is_canceled_before_exit(self):
        strategy, trading, _ = build_strategy()
        position = build_position("open1", 100.0, 100.0)
        position.status = OrderStatus.OPEN
        strategy.position_manager.add_position(position)
        trading.queue_query(
            OrderResult(
                custom_id="open1",
                symbol=SYMBOL,
                side=OrderSide.BUY,
                status=OrderStatus.OPEN,
                quantity=100.0,
            )
        )
        set_latest_kline(strategy, 101.1)

        closed_positions = strategy.check_close_position()

        assert closed_positions == [position]
        assert trading.canceled == ["open1"], "未成交的入场单必须先撤销"

    def test_filled_exit_order_removes_position(self):
        """止盈单已成交: 订单进入历史, 不再重复平仓"""
        strategy, trading, _ = build_strategy()
        position = build_position("t1", 100.0, 100.0)
        position.exit_id = "exit1"
        position.exit_price = 101.0
        strategy.position_manager.add_position(position)
        trading.queue_query(
            OrderResult(
                custom_id="exit1",
                symbol=SYMBOL,
                side=OrderSide.SELL,
                status=OrderStatus.CLOSED,
                quantity=100.0,
                filled_qty=100.0,
            )
        )
        set_latest_kline(strategy, 101.1)

        closed_positions = strategy.check_close_position()

        assert closed_positions == [position]
        assert trading.placed == [], "已成交的止盈单不应再次下单"


class TestPartialFillEntry:
    """入场单部分成交的处理: 撤剩余挂单后, 已成交部分照样平仓"""

    def test_partial_filled_entry_is_closed_by_filled_amount(self):
        strategy, trading, _ = build_strategy()
        position = GridPosition(
            entry_id="p1",
            side=OrderSide.BUY,
            price=100.0,
            quantity=100.0,
            fixed_take_profit_rate=0.01,
            signal_min_take_profit_rate=0.002,
            status=OrderStatus.OPEN,
        )
        strategy.position_manager.add_position(position)
        set_latest_kline(strategy, 101.1)
        trading.queue_query(
            OrderResult(
                custom_id="p1",
                symbol=SYMBOL,
                side=OrderSide.BUY,
                status=OrderStatus.OPEN,
                quantity=100.0,
                filled_qty=40.0,
                price=100.0,
            ),
            OrderResult(
                custom_id="p1",
                symbol=SYMBOL,
                side=OrderSide.BUY,
                status=OrderStatus.CANCELED,
                quantity=100.0,
                filled_qty=40.0,
                price=100.0,
            ),
        )

        closed_positions = strategy.check_close_position()

        assert trading.canceled == ["p1"], "必须先撤掉剩余挂单, 防止继续建仓"
        assert closed_positions == [position]
        assert position.filled_quantity == pytest.approx(40.0), "查询结果必须回写成交量"
        assert len(trading.placed) == 1, "已成交部分必须挂出平仓单, 不能丢敞口"
        exit_request = trading.placed[0]
        assert exit_request.side is OrderSide.SELL
        assert exit_request.quantity == pytest.approx(40.0), "平仓量 = 实际成交量"

    def test_zero_filled_entry_is_still_dropped(self):
        """零成交入场单: 撤单后直接丢弃, 不产生平仓单"""
        strategy, trading, _ = build_strategy()
        position = GridPosition(
            entry_id="p1",
            side=OrderSide.BUY,
            price=100.0,
            quantity=100.0,
            fixed_take_profit_rate=0.01,
            signal_min_take_profit_rate=0.002,
            status=OrderStatus.OPEN,
        )
        strategy.position_manager.add_position(position)
        set_latest_kline(strategy, 101.1)
        trading.queue_query(
            OrderResult(
                custom_id="p1",
                symbol=SYMBOL,
                side=OrderSide.BUY,
                status=OrderStatus.OPEN,
                quantity=100.0,
                filled_qty=0.0,
                price=100.0,
            ),
            OrderResult(
                custom_id="p1",
                symbol=SYMBOL,
                side=OrderSide.BUY,
                status=OrderStatus.CANCELED,
                quantity=100.0,
                filled_qty=0.0,
                price=100.0,
            ),
        )

        closed_positions = strategy.check_close_position()

        assert trading.canceled == ["p1"]
        assert closed_positions == [position]
        assert trading.placed == [], "零成交没有可平仓位"


class TestExitOrderReplacement:
    """出场单部分成交后的重挂: 必须扣除已成交部分"""

    def build_position_with_exit(self) -> GridPosition:
        position = build_position("p1", 100.0, 100.0)
        position.exit_id = "x1"
        position.exit_price = 101.0
        return position

    def test_replacement_subtracts_filled_amount(self):
        strategy, trading, _ = build_strategy()
        position = self.build_position_with_exit()
        strategy.position_manager.add_position(position)
        set_latest_kline(strategy, 101.1)
        trading.queue_query(
            OrderResult(
                custom_id="x1",
                symbol=SYMBOL,
                side=OrderSide.SELL,
                status=OrderStatus.OPEN,
                quantity=100.0,
                filled_qty=30.0,
                price=101.0,
            ),
            OrderResult(
                custom_id="x1",
                symbol=SYMBOL,
                side=OrderSide.SELL,
                status=OrderStatus.CANCELED,
                quantity=100.0,
                filled_qty=30.0,
                price=101.0,
            ),
        )

        strategy.check_close_position()

        assert trading.canceled == ["x1"], "未成交完的旧出场单必须先撤"
        assert len(trading.placed) == 1
        assert trading.placed[0].quantity == pytest.approx(70.0), (
            "重挂量必须扣除已成交的 30"
        )

    def test_replacement_keeps_full_quantity_when_unfilled(self):
        """旧出场单零成交被撤: 按原量重挂"""
        strategy, trading, _ = build_strategy()
        position = self.build_position_with_exit()
        strategy.position_manager.add_position(position)
        set_latest_kline(strategy, 101.1)
        trading.queue_query(
            OrderResult(
                custom_id="x1",
                symbol=SYMBOL,
                side=OrderSide.SELL,
                status=OrderStatus.OPEN,
                quantity=100.0,
                filled_qty=0.0,
                price=101.0,
            ),
            OrderResult(
                custom_id="x1",
                symbol=SYMBOL,
                side=OrderSide.SELL,
                status=OrderStatus.CANCELED,
                quantity=100.0,
                filled_qty=0.0,
                price=101.0,
            ),
        )

        strategy.check_close_position()

        assert trading.placed[0].quantity == pytest.approx(100.0)

    def test_stale_fully_filled_exit_is_archived_not_replaced(self):
        """出场单实际已全部成交(撤单复查才发现): 直接归档, 不重复下单"""
        strategy, trading, _ = build_strategy()
        position = self.build_position_with_exit()
        strategy.position_manager.add_position(position)
        set_latest_kline(strategy, 101.1)
        trading.queue_query(
            OrderResult(
                custom_id="x1",
                symbol=SYMBOL,
                side=OrderSide.SELL,
                status=OrderStatus.OPEN,
                quantity=100.0,
                filled_qty=100.0,
                price=101.0,
            ),
            OrderResult(
                custom_id="x1",
                symbol=SYMBOL,
                side=OrderSide.SELL,
                status=OrderStatus.CANCELED,
                quantity=100.0,
                filled_qty=100.0,
                price=101.0,
            ),
        )

        closed_positions = strategy.check_close_position()

        assert closed_positions == [position]
        assert trading.placed == [], "实际已全平, 不能再挂单"


class TestRealTimeTakeProfit:
    """take_profit_use_limit_order: 入场成交后立刻挂出反向限价止盈单"""

    def build_limit_tp_strategy(
        self,
    ) -> tuple[SignalGridStrategy, FakeTradingPort]:
        config = build_config(take_profit_use_limit_order=True)
        strategy, trading, _ = build_strategy(config)
        position = build_position("entry1", 100.0, 100.0)
        position.status = OrderStatus.OPEN
        strategy.position_manager.add_position(position)
        return strategy, trading

    def test_places_exit_order_after_entry_filled(self):
        strategy, trading = self.build_limit_tp_strategy()
        trading.queue_query(
            OrderResult(
                custom_id="entry1",
                symbol=SYMBOL,
                side=OrderSide.BUY,
                status=OrderStatus.CLOSED,
                quantity=100.0,
                filled_qty=100.0,
            )
        )
        set_latest_kline(strategy, 100.0)

        strategy._on_kline()

        assert len(trading.placed) == 1, "入场成交后必须挂出止盈单"
        request = trading.placed[0]
        assert request.side is OrderSide.SELL
        assert request.price == pytest.approx(101.0), "止盈价 = 入场价 * (1 + 1%)"
        assert request.quantity == pytest.approx(100.0), "全额止盈"
        assert strategy.position_manager.positions[0].exit_id == request.custom_id

    def test_tracks_filled_exit_order(self):
        """止盈单成交后从当前订单移除, 不再重复处理"""
        strategy, trading = self.build_limit_tp_strategy()
        position = strategy.position_manager.positions[0]
        position.status = OrderStatus.CLOSED
        position.exit_id = "exit1"
        position.exit_price = 101.0
        trading.queue_query(
            OrderResult(
                custom_id="exit1",
                symbol=SYMBOL,
                side=OrderSide.SELL,
                status=OrderStatus.CLOSED,
                quantity=100.0,
                filled_qty=100.0,
            )
        )
        # 价格区间必须覆盖止盈价, 否则不会去查询止盈单状态
        set_latest_kline_range(strategy, 100.0, high=101.5, low=99.5)

        strategy._on_kline()

        assert strategy.position_manager.positions == [], "止盈完成后订单应移出当前持仓"
        assert trading.placed == []

    def test_does_not_place_exit_while_entry_still_open(self):
        strategy, trading = self.build_limit_tp_strategy()
        trading.queue_query(
            OrderResult(
                custom_id="entry1",
                symbol=SYMBOL,
                side=OrderSide.BUY,
                status=OrderStatus.OPEN,
                quantity=100.0,
            )
        )
        set_latest_kline(strategy, 100.0)

        strategy._on_kline()

        assert trading.placed == [], "入场未成交时不能提前挂止盈单"


class TestJournalIntegration:
    """策略与账本的集成: 下单记账 / 重启恢复 / 启动对账"""

    def test_normal_place_order_is_recorded_in_journal(self):
        """NORMAL 下单必须同一笔进入账本 (记账包装自动完成)"""
        journal = OrderJournal(":memory:")
        strategy, _, _ = build_strategy(journal=journal)
        set_latest_kline(strategy, 100.0)

        assert strategy.check_open_position() is True

        rows = journal.load_positions(STRATEGY_KEY)
        assert len(rows) == 1, "挂上的入场单必须落账"
        assert rows[0].status is OrderStatus.OPEN
        assert rows[0].fixed_take_profit_rate == pytest.approx(0.01), (
            "策略参数必须随意图快照入账"
        )

    def test_orders_survive_restart(self):
        """重启: 同一账本上重建策略, 订单状态完整恢复"""
        journal = OrderJournal(":memory:")
        strategy, _, _ = build_strategy(journal=journal)
        position = build_position("t1", 100.0, 100.0)
        position.status = OrderStatus.OPEN
        strategy.position_manager.add_position(position)
        strategy.position_manager.record_positions(refresh_orders=True)

        reloaded, _, _ = build_strategy(journal=journal)

        restored = reloaded.position_manager.positions[0]
        assert restored.status is OrderStatus.OPEN, "状态必须还原为 OrderStatus 枚举"
        assert restored.entry_id == "t1"

    def test_startup_reconcile_recovers_crashed_order(self):
        """崩溃遗留的下单: 启动时用订单号去交易所确认并恢复到账本"""
        journal = OrderJournal(":memory:")
        journal.reserve(STRATEGY_KEY, make_entry_intent())
        trading = FakeTradingPort()
        trading.queue_query(
            OrderResult(
                custom_id="BUYrecovered",
                symbol=SYMBOL,
                side=OrderSide.BUY,
                status=OrderStatus.OPEN,
                quantity=100.0,
                price=100.0,
            )
        )

        strategy = SignalGridStrategy(
            build_config(),
            trading,
            FakeMarketDataPort(),
            journal,
            reconcile_min_age_seconds=0.0,
        )

        orders = strategy.position_manager.positions
        assert [position.entry_id for position in orders] == ["BUYrecovered"], (
            "对账恢复的订单必须进入策略订单列表"
        )
        assert journal.pending_ids(STRATEGY_KEY) == [], "恢复后必须核销"
