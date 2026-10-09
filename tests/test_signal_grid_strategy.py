"""SignalGridStrategy（网格策略）测试

覆盖: 派生量（数量/均价/相位）/ 入场记账 / 出场生命周期 / 部分成交 /
实时止盈 / 账本集成 / 孤儿回收。
"""

from datetime import UTC, datetime

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
from smart_trader.storage.records import OrderRecord
from smart_trader.strategy.signal_grid_strategy import (
    GridPosition,
    SignalGridStrategy,
    SignalGridStrategyConfig,
)
from tests.fakes import (
    FakeMarketDataPort,
    FakeTradingPort,
    make_order_record,
    make_position_row,
)

TIMEFRAME = "1m"
SYMBOL = Symbol(base="BTC", quote="USDT")
STRATEGY_KEY = "btcusdt_test_1m"


def set_latest_kline(strategy: SignalGridStrategy, close: float) -> None:
    set_latest_kline_range(strategy, close, close, close)


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


def make_entry(
    order_id: str,
    position_id: str,
    side: OrderSide,
    filled_qty: float,
    price: float,
    status: OrderStatus = OrderStatus.CLOSED,
    quantity: float | None = None,
) -> OrderRecord:
    return OrderRecord(
        order_id=order_id,
        grid_position_id=position_id,
        strategy_key=STRATEGY_KEY,
        symbol=SYMBOL.ccxt(),
        side=side,
        type=OrderType.LIMIT,
        price=price,
        quantity=quantity if quantity is not None else max(filled_qty, 1.0),
        filled_qty=filled_qty,
        avg_price=price,
        status=status,
    )


def make_exit(
    order_id: str,
    position_id: str,
    side: OrderSide,
    price: float,
    filled_qty: float = 0.0,
    status: OrderStatus = OrderStatus.OPEN,
    quantity: float | None = None,
) -> OrderRecord:
    return OrderRecord(
        order_id=order_id,
        grid_position_id=position_id,
        strategy_key=STRATEGY_KEY,
        symbol=SYMBOL.ccxt(),
        side=side,
        type=OrderType.LIMIT,
        price=price,
        quantity=quantity if quantity is not None else max(filled_qty, 1.0),
        filled_qty=filled_qty,
        avg_price=price if filled_qty > 0 else None,
        status=status,
    )


def build_position(
    position_id: str,
    price: float,
    quantity: float,
    side: OrderSide = OrderSide.BUY,
    filled: float | None = None,
    status: OrderStatus = OrderStatus.CLOSED,
) -> GridPosition:
    """构造一个含单条入场单的逻辑仓位（派生数量 = filled, 派生均价 = price）"""
    filled = quantity if filled is None else filled
    position_side = (
        PositionSide.LONG if side == OrderSide.BUY else PositionSide.SHORT
    )
    return GridPosition(
        id=position_id,
        strategy_key=STRATEGY_KEY,
        symbol=SYMBOL.ccxt(),
        position_side=position_side,
        main_order_side=side,
        fixed_take_profit_rate=0.01,
        signal_min_take_profit_rate=0.002,
        orders=[make_entry(f"{position_id}-e", position_id, side, filled, price, status=status)],
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


def seed_position(
    journal: OrderJournal,
    position_id: str = "POS1",
    entry_filled: float = 100.0,
    entry_price: float = 100.0,
    entry_status: OrderStatus = OrderStatus.CLOSED,
    exit_order: OrderRecord | None = None,
    created_at: str = "",
) -> None:
    """把仓位与订单直接写进账本（模拟上次运行留下的状态）"""
    journal.upsert_position(
        make_position_row(
            position_id,
            strategy_key=STRATEGY_KEY,
            symbol=SYMBOL.ccxt(),
            created_at=created_at,
        )
    )
    journal.append_order(
        make_order_record(
            f"{position_id}-e",
            position_id,
            strategy_key=STRATEGY_KEY,
            symbol=SYMBOL.ccxt(),
            side=OrderSide.BUY,
            price=entry_price,
            quantity=entry_filled,
            filled_qty=entry_filled,
            avg_price=entry_price,
            status=entry_status,
        )
    )
    if exit_order is not None:
        journal.append_order(exit_order)


class TestDerivedQuantity:
    def test_position_derives_quantity_price_and_phase(self):
        position = build_position("POS1", 100.0, 100.0)
        assert position.quantity == pytest.approx(100.0)
        assert position.price == pytest.approx(100.0)
        assert position.phase == "holding"

    def test_opening_quantity_and_price_contribute_to_basis(self):
        position = build_position("POS1", 100.0, 100.0)
        position.opening_quantity = 50.0
        position.opening_price = 90.0

        assert position.quantity == pytest.approx(150.0)
        assert position.price == pytest.approx((90.0 * 50 + 100.0 * 100) / 150)

    def test_quantity_nets_entry_and_exit_fills(self):
        position = build_position("POS1", 100.0, 100.0)
        position.orders.append(
            make_exit("POS1-x", "POS1", OrderSide.SELL, 101.0, filled_qty=40.0)
        )
        assert position.quantity == pytest.approx(60.0)

    def test_phase_is_opening_when_flat(self):
        position = build_position("POS1", 100.0, 100.0, filled=0.0)
        assert position.phase == "opening"

    def test_phase_is_closing_with_pending_exit(self):
        position = build_position("POS1", 100.0, 100.0)
        position.orders.append(
            make_exit("POS1-x", "POS1", OrderSide.SELL, 101.0, status=OrderStatus.OPEN)
        )
        assert position.phase == "closing"

    def test_phase_is_closed_when_inactive(self):
        position = build_position("POS1", 100.0, 100.0)
        position.active = False
        assert position.phase == "closed"

    def test_short_position_uses_reversed_entry_side(self):
        position = build_position("POS1", 100.0, 100.0, side=OrderSide.SELL)
        assert position.entry_side is OrderSide.SELL
        assert position.exit_side is OrderSide.BUY


class TestCloseFullQuantity:
    """平仓恒定按派生数量全额执行"""

    def test_config_no_longer_exposes_close_position_ratio(self):
        assert "close_position_ratio" not in SignalGridStrategyConfig.model_fields

    def test_closes_full_quantity(self):
        strategy, trading, _ = build_strategy()
        strategy.position_manager.add_position(build_position("POS1", 100.0, 100.0))
        set_latest_kline(strategy, 101.1)

        strategy.check_close_position()

        assert trading.placed[0].quantity == pytest.approx(100.0)

    def test_each_position_gets_its_own_exit_order(self):
        strategy, trading, _ = build_strategy()
        first = build_position("POS1", 100.0, 100.0)
        second = build_position("POS2", 99.0, 50.0)
        strategy.position_manager.add_position(first)
        strategy.position_manager.add_position(second)
        set_latest_kline(strategy, 101.1)

        strategy.check_close_position()

        assert len(trading.placed) == 2, "每仓位各一张出场单"
        assert sorted(request.quantity for request in trading.placed) == [50.0, 100.0]
        assert first.pending_exit_order is not None
        assert second.pending_exit_order is not None
        assert first.pending_exit_order.order_id != second.pending_exit_order.order_id

    def test_exit_order_is_reversal_side(self):
        strategy, trading, _ = build_strategy()
        strategy.position_manager.add_position(build_position("POS1", 100.0, 100.0))
        set_latest_kline(strategy, 101.1)

        strategy.check_close_position()

        request = trading.placed[0]
        assert request.side is OrderSide.SELL, "买单的平仓单必须是卖单"
        assert request.type is OrderType.LIMIT
        assert request.position_side is PositionSide.LONG


class TestExitQuantityUsesFilledAmount:
    """平仓数量必须按成交数量计算, 否则部分成交时会超卖"""

    def test_partial_fill_closes_only_filled_quantity(self):
        strategy, trading, _ = build_strategy()
        strategy.position_manager.add_position(
            build_position("POS1", 100.0, 100.0, filled=30.0)
        )
        set_latest_kline(strategy, 101.1)

        strategy.check_close_position()

        assert trading.placed[0].quantity == pytest.approx(30.0), (
            "只有 30 成交, 平仓就只能是 30"
        )

    def test_partially_filled_orders_each_close_their_own_amount(self):
        strategy, trading, _ = build_strategy()
        strategy.position_manager.add_position(
            build_position("POS1", 100.0, 100.0, filled=30.0)
        )
        strategy.position_manager.add_position(
            build_position("POS2", 99.0, 50.0, filled=50.0)
        )
        set_latest_kline(strategy, 101.1)

        strategy.check_close_position()

        assert len(trading.placed) == 2
        assert sorted(request.quantity for request in trading.placed) == [30.0, 50.0]

    def test_fully_filled_order_closes_full_quantity(self):
        strategy, trading, _ = build_strategy()
        strategy.position_manager.add_position(
            build_position("POS1", 100.0, 100.0, filled=100.0)
        )
        set_latest_kline(strategy, 101.1)

        strategy.check_close_position()

        assert trading.placed[0].quantity == pytest.approx(100.0)

    def test_unfilled_order_does_not_place_exit(self):
        """入场单还没成交就不该平仓"""
        strategy, trading, _ = build_strategy()
        strategy.position_manager.add_position(
            build_position(
                "POS1", 100.0, 100.0, filled=0.0, status=OrderStatus.CANCELED
            )
        )
        set_latest_kline(strategy, 101.1)

        strategy.check_close_position()

        assert trading.placed == [], "未成交的订单不应触发平仓"

    def test_real_time_take_profit_uses_filled_quantity(self):
        config = build_config(take_profit_use_limit_order=True)
        strategy, trading, _ = build_strategy(config)
        strategy.position_manager.add_position(
            build_position("POS1", 100.0, 100.0, filled=40.0)
        )
        set_latest_kline(strategy, 100.0)

        strategy._on_kline()

        assert trading.placed[0].quantity == pytest.approx(40.0), (
            "止盈数量 = 成交量"
        )


class TestOpenOrder:
    def test_records_exchange_side_effects(self):
        """入场必须落账, 派生数量 / 均价以交易所成交为准"""
        journal = OrderJournal(":memory:")
        strategy, trading, _ = build_strategy(journal=journal)
        trading.queue_place(
            OrderResult(
                custom_id="echo",
                symbol=SYMBOL,
                side=OrderSide.BUY,
                status=OrderStatus.CLOSED,
                quantity=100.0,
                filled_qty=100.0,
                avg_price=100.0,
            )
        )
        set_latest_kline(strategy, 100.0)

        assert strategy.check_open_position() is True

        position = strategy.position_manager.positions[0]
        assert position.quantity == pytest.approx(100.0)
        assert position.price == pytest.approx(100.0)
        rows = journal.load_active_positions(STRATEGY_KEY)
        assert len(rows) == 1, "首单必须同事务建仓位"

    def test_does_not_open_when_price_outside_grid_range(self):
        strategy, trading, _ = build_strategy(build_config(lowest_price=200.0))
        set_latest_kline(strategy, 100.0)

        assert strategy.check_open_position() is False
        assert trading.placed == []

    def test_does_not_open_when_max_order_reached(self):
        strategy, trading, _ = build_strategy(build_config(max_order=1))
        strategy.position_manager.add_position(build_position("POS1", 100.0, 100.0))
        set_latest_kline(strategy, 100.0)

        assert strategy.check_open_position() is False
        assert trading.placed == []


class TestChaserBehavior:
    def test_chaser_open_is_used_when_configured(self):
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
        """交易所查不到入场单: 视为过期, 成交部分照常挂出平仓单"""
        strategy, trading, _ = build_strategy()
        position = build_position("POS1", 100.0, 100.0)
        position.orders[0].status = OrderStatus.OPEN
        strategy.position_manager.add_position(position)
        trading.queue_query(None)
        set_latest_kline(strategy, 101.1)

        archived = strategy.check_close_position()

        assert archived == [], "挂出出场单不等于平仓完成"
        assert position.orders[0].status is OrderStatus.EXPIRED
        assert trading.canceled == [], "订单已不存在, 无需撤单"
        assert len(trading.placed) == 1, "已成交部分必须挂出平仓单"

    def test_open_entry_order_is_canceled_before_exit(self):
        strategy, trading, _ = build_strategy()
        position = build_position("POS1", 100.0, 100.0, status=OrderStatus.OPEN)
        strategy.position_manager.add_position(position)
        trading.queue_query(
            OrderResult(
                custom_id="POS1-e",
                symbol=SYMBOL,
                side=OrderSide.BUY,
                status=OrderStatus.OPEN,
                quantity=100.0,
                filled_qty=100.0,
            )
        )
        set_latest_kline(strategy, 101.1)

        archived = strategy.check_close_position()

        assert archived == [position], "撤单后剩余数量为 0, 无可平仓位"
        assert trading.canceled == ["POS1-e"], "未成交的入场单必须先撤销"

    def test_filled_exit_order_is_archived_by_tracking(self):
        """出场单已成交: 跟踪时结算并归档, 不再重复平仓"""
        strategy, trading, _ = build_strategy()
        position = build_position("POS1", 100.0, 100.0)
        position.orders.append(
            make_exit("POS1-x", "POS1", OrderSide.SELL, 101.0)
        )
        strategy.position_manager.add_position(position)
        trading.queue_query(
            OrderResult(
                custom_id="POS1-x",
                symbol=SYMBOL,
                side=OrderSide.SELL,
                status=OrderStatus.CLOSED,
                quantity=100.0,
                filled_qty=100.0,
            )
        )
        set_latest_kline(strategy, 101.1)

        archived, changed = strategy._track_pending_exits()

        assert archived == [position]
        assert changed is True
        assert position.quantity == pytest.approx(0.0), "成交量必须结算"
        assert trading.placed == [], "已成交的止盈单不应再次下单"


class TestPartialFillEntry:
    """入场单部分成交的处理: 撤剩余挂单后, 已成交部分照样平仓"""

    def test_partial_filled_entry_is_closed_by_filled_amount(self):
        strategy, trading, _ = build_strategy()
        position = build_position(
            "POS1", 100.0, 100.0, filled=0.0, status=OrderStatus.OPEN
        )
        strategy.position_manager.add_position(position)
        set_latest_kline(strategy, 101.1)
        trading.queue_query(
            OrderResult(
                custom_id="POS1-e",
                symbol=SYMBOL,
                side=OrderSide.BUY,
                status=OrderStatus.OPEN,
                quantity=100.0,
                filled_qty=40.0,
            ),
            OrderResult(
                custom_id="POS1-e",
                symbol=SYMBOL,
                side=OrderSide.BUY,
                status=OrderStatus.CANCELED,
                quantity=100.0,
                filled_qty=40.0,
            ),
        )

        archived = strategy.check_close_position()

        assert trading.canceled == ["POS1-e"], "必须先撤掉剩余挂单, 防止继续建仓"
        assert archived == [], "挂出出场单不等于平仓完成"
        assert position.quantity == pytest.approx(40.0), "查询结果必须回写成交量"
        assert len(trading.placed) == 1, "已成交部分必须挂出平仓单, 不能丢敞口"
        exit_request = trading.placed[0]
        assert exit_request.side is OrderSide.SELL
        assert exit_request.quantity == pytest.approx(40.0), "平仓量 = 成交量"

    def test_zero_filled_entry_is_still_dropped(self):
        """零成交入场单: 撤单后直接丢弃, 不产生平仓单"""
        strategy, trading, _ = build_strategy()
        position = build_position(
            "POS1", 100.0, 100.0, filled=0.0, status=OrderStatus.OPEN
        )
        strategy.position_manager.add_position(position)
        set_latest_kline(strategy, 101.1)
        trading.queue_query(
            OrderResult(
                custom_id="POS1-e",
                symbol=SYMBOL,
                side=OrderSide.BUY,
                status=OrderStatus.OPEN,
                quantity=100.0,
                filled_qty=0.0,
            ),
            OrderResult(
                custom_id="POS1-e",
                symbol=SYMBOL,
                side=OrderSide.BUY,
                status=OrderStatus.CANCELED,
                quantity=100.0,
                filled_qty=0.0,
            ),
        )

        archived = strategy.check_close_position()

        assert trading.canceled == ["POS1-e"]
        assert archived == [position]
        assert trading.placed == [], "零成交没有可平仓位"


class TestRestingExitLifecycle:
    """已挂出未成交的出场单: 保留 / 强制出场覆盖 / 成交后归档"""

    def build_position_with_exit(self) -> GridPosition:
        position = build_position("POS1", 100.0, 100.0)
        position.orders.append(make_exit("POS1-x", "POS1", OrderSide.SELL, 101.0))
        return position

    def test_resting_exit_is_kept_when_tp_still_triggered(self):
        """止盈条件仍在, 但已有未成交出场单: 保留原挂单, 不撤单不重挂"""
        strategy, trading, _ = build_strategy()
        position = self.build_position_with_exit()
        strategy.position_manager.add_position(position)
        set_latest_kline(strategy, 101.1)
        trading.queue_query(
            OrderResult(
                custom_id="POS1-x",
                symbol=SYMBOL,
                side=OrderSide.SELL,
                status=OrderStatus.OPEN,
                quantity=100.0,
                filled_qty=0.0,
                price=101.0,
            )
        )

        strategy.check_close_position()

        assert trading.canceled == [], "保留原挂单, 不得撤单"
        assert trading.placed == [], "已有挂单, 不得重复下单"
        assert position.pending_exit_order is not None
        assert position.pending_exit_order.order_id == "POS1-x"

    def test_force_stop_loss_settles_partial_and_replaces_remainder(self):
        """强制止损覆盖休息挂单: 撤单结算已成交30, 只重挂剩余70"""
        strategy, trading, _ = build_strategy(
            build_config(enable_order_stop_loss=True, order_stop_loss_rate=0.05)
        )
        position = build_position("POS1", 100.0, 100.0)
        position.enable_stop_loss = True
        position.trailing_stop_price = 95.0
        position.orders.append(make_exit("POS1-x", "POS1", OrderSide.SELL, 101.0))
        strategy.position_manager.add_position(position)
        set_latest_kline(strategy, 94.0)  # 触发止损
        trading.queue_query(
            OrderResult(
                custom_id="POS1-x",
                symbol=SYMBOL,
                side=OrderSide.SELL,
                status=OrderStatus.CANCELED,
                quantity=100.0,
                filled_qty=30.0,
                price=101.0,
            )
        )

        strategy.check_close_position()

        assert trading.canceled == ["POS1-x"], "强制出场必须先撤掉休息中的挂单"
        assert position.quantity == pytest.approx(70.0), "已成交30必须结算扣减"
        assert len(trading.placed) == 1
        assert trading.placed[0].quantity == pytest.approx(70.0), "只重挂剩余"

    def test_stale_fully_filled_exit_is_archived_not_replaced(self):
        """出场单实际已全部成交(撤单复查才发现): 归档, 不重复下单"""
        strategy, trading, _ = build_strategy(
            build_config(enable_order_stop_loss=True, order_stop_loss_rate=0.05)
        )
        position = self.build_position_with_exit()
        position.enable_stop_loss = True
        position.trailing_stop_price = 95.0
        strategy.position_manager.add_position(position)
        set_latest_kline(strategy, 94.0)
        trading.queue_query(
            OrderResult(
                custom_id="POS1-x",
                symbol=SYMBOL,
                side=OrderSide.SELL,
                status=OrderStatus.CLOSED,
                quantity=100.0,
                filled_qty=100.0,
                price=101.0,
            )
        )

        archived = strategy.check_close_position()

        assert archived == [position], "出场单实际已全平, 直接归档"
        assert trading.placed == [], "实际已全平, 不能再挂单"


class TestRealTimeTakeProfit:
    """take_profit_use_limit_order: 入场成交后立刻挂出反向限价止盈单"""

    def build_limit_tp_strategy(
        self,
    ) -> tuple[SignalGridStrategy, FakeTradingPort]:
        config = build_config(take_profit_use_limit_order=True)
        strategy, trading, _ = build_strategy(config)
        position = build_position(
            "POS1", 100.0, 100.0, filled=0.0, status=OrderStatus.OPEN
        )
        strategy.position_manager.add_position(position)
        return strategy, trading

    def test_places_exit_order_after_entry_filled(self):
        strategy, trading = self.build_limit_tp_strategy()
        trading.queue_query(
            OrderResult(
                custom_id="POS1-e",
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
        assert strategy.position_manager.positions[0].pending_exit_order is not None
        assert (
            strategy.position_manager.positions[0].pending_exit_order.order_id
            == request.custom_id
        )

    def test_tracks_filled_exit_order(self):
        """止盈单成交后从当前持仓移除, 不再重复处理"""
        strategy, trading = self.build_limit_tp_strategy()
        position = strategy.position_manager.positions[0]
        position.orders[0].status = OrderStatus.CLOSED
        position.orders[0].filled_qty = 100.0
        position.orders[0].avg_price = 100.0
        position.orders.append(
            make_exit("POS1-x", "POS1", OrderSide.SELL, 101.0)
        )
        trading.queue_query(
            OrderResult(
                custom_id="POS1-x",
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

        assert strategy.position_manager.positions == [], "止盈完成后仓位应移出持仓"

    def test_does_not_place_exit_while_entry_still_open(self):
        strategy, trading = self.build_limit_tp_strategy()
        trading.queue_query(
            OrderResult(
                custom_id="POS1-e",
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

        rows = journal.load_active_positions(STRATEGY_KEY)
        assert len(rows) == 1, "挂上的入场单必须落账"
        assert rows[0].fixed_take_profit_rate == pytest.approx(0.01)
        orders = journal.load_orders_for_positions([rows[0].id])
        assert len(orders) == 1 and orders[0].status is OrderStatus.OPEN

    def test_orders_survive_restart(self):
        """重启: 同一账本上重建策略, 订单状态完整恢复"""
        journal = OrderJournal(":memory:")
        seed_position(journal, "POS1", entry_filled=100.0, entry_status=OrderStatus.OPEN)

        reloaded, _, _ = build_strategy(journal=journal)

        restored = reloaded.position_manager.positions[0]
        assert restored.orders[0].status is OrderStatus.OPEN
        assert restored.quantity == pytest.approx(100.0)

    def test_startup_reconcile_recovers_crashed_order(self):
        """崩溃遗留的下单: 启动时用订单行去交易所确认并恢复到账本"""
        journal = OrderJournal(":memory:")
        journal.upsert_position(
            make_position_row(
                "POS1", strategy_key=STRATEGY_KEY, symbol=SYMBOL.ccxt()
            )
        )
        journal.append_order(
            make_order_record(
                "BUYu1",
                "POS1",
                strategy_key=STRATEGY_KEY,
                symbol=SYMBOL.ccxt(),
                status=None,
            )
        )
        trading = FakeTradingPort()
        trading.queue_query(
            OrderResult(
                custom_id="BUYu1",
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

        positions = strategy.position_manager.positions
        assert [position.id for position in positions] == ["POS1"]
        assert positions[0].orders[0].status is OrderStatus.OPEN
        assert journal.pending_orders(STRATEGY_KEY) == [], "恢复后必须回写"


class TestExitLifecycle:
    """出场是有生命周期的: 挂单 != 了结, 确认成交才归档"""

    def test_exit_order_is_not_archived_until_filled(self):
        journal = OrderJournal(":memory:")
        seed_position(journal, "POS1")
        strategy, _, _ = build_strategy(
            build_config(lowest_price=200.0), journal
        )
        set_latest_kline(strategy, 101.1)

        strategy._on_kline_finished()

        positions = strategy.position_manager.positions
        assert [position.id for position in positions] == ["POS1"]
        assert positions[0].has_pending_exit
        assert [row.id for row in journal.load_active_positions(STRATEGY_KEY)] == [
            "POS1"
        ], "未平仓的仓位必须保持 active, 重启后才能恢复"

    def test_position_is_archived_only_after_exit_filled(self):
        journal = OrderJournal(":memory:")
        seed_position(journal, "POS1")
        strategy, trading, _ = build_strategy(
            build_config(lowest_price=200.0), journal
        )
        set_latest_kline(strategy, 101.1)
        strategy._on_kline_finished()  # 挂出出场单
        position = strategy.position_manager.positions[0]
        assert position.has_pending_exit

        trading.queue_query(
            OrderResult(
                custom_id=position.pending_exit_order.order_id,  # type: ignore[union-attr]
                symbol=SYMBOL,
                side=OrderSide.SELL,
                status=OrderStatus.CLOSED,
                quantity=100.0,
                filled_qty=100.0,
            )
        )
        strategy._on_kline_finished()

        assert strategy.position_manager.positions == [], "出场确认成交后才归档"
        assert journal.load_active_positions(STRATEGY_KEY) == []

    def test_resting_exit_survives_price_pullback(self):
        """价格回落且不再触发止盈: 仓位仍被跟踪, 原挂单保留"""
        strategy, trading, _ = build_strategy()
        strategy.position_manager.add_position(build_position("POS1", 100.0, 100.0))
        set_latest_kline(strategy, 101.1)
        strategy._on_kline_finished()  # 挂出出场单
        position = strategy.position_manager.positions[0]
        assert position.has_pending_exit

        set_latest_kline(strategy, 100.5)  # 回落到止盈线以下
        trading.queue_query(
            OrderResult(
                custom_id=position.pending_exit_order.order_id,  # type: ignore[union-attr]
                symbol=SYMBOL,
                side=OrderSide.SELL,
                status=OrderStatus.OPEN,
                quantity=100.0,
                filled_qty=0.0,
            )
        )
        strategy._on_kline_finished()

        assert [position.id for position in strategy.position_manager.positions] == [
            "POS1"
        ]
        assert trading.canceled == [], "价格回落也必须保留原挂单"

    def test_canceled_exit_reverts_to_holding(self):
        strategy, trading, _ = build_strategy()
        position = build_position("POS1", 100.0, 100.0)
        position.orders.append(make_exit("POS1-x", "POS1", OrderSide.SELL, 101.0))
        strategy.position_manager.add_position(position)
        set_latest_kline(strategy, 100.5)  # 未触发止盈, 隔离观察退回持仓
        trading.queue_query(
            OrderResult(
                custom_id="POS1-x",
                symbol=SYMBOL,
                side=OrderSide.SELL,
                status=OrderStatus.CANCELED,
                quantity=100.0,
                filled_qty=0.0,
            )
        )

        strategy._on_kline_finished()

        assert position.pending_exit_order is None, "作废的出场单必须清除, 等待重挂"
        assert position.phase == "holding"
        assert [position.id for position in strategy.position_manager.positions] == [
            "POS1"
        ]

    def test_missing_exit_order_is_confirmed_by_cancel(self):
        """query 返回 None 时不直接清 exit, 必须撤单复核确认不存在"""
        strategy, trading, _ = build_strategy()
        position = build_position("POS1", 100.0, 100.0)
        position.orders.append(make_exit("POS1-x", "POS1", OrderSide.SELL, 101.0))
        strategy.position_manager.add_position(position)
        set_latest_kline(strategy, 100.5)
        trading.queue_query(None, None)  # 初次查询与撤单复查均查不到

        strategy._on_kline_finished()

        assert trading.canceled == ["POS1-x"], "必须撤单复核"
        assert position.pending_exit_order is None
        assert trading.placed == [], "确认不存在后才退回持仓, 不得重复下单"

    def test_query_error_keeps_pending_exit(self):
        strategy, trading, _ = build_strategy()
        position = build_position("POS1", 100.0, 100.0)
        position.orders.append(make_exit("POS1-x", "POS1", OrderSide.SELL, 101.0))
        strategy.position_manager.add_position(position)
        set_latest_kline(strategy, 100.5)
        trading.queue_query(
            SmartTraderError(ErrorCode.EXCHANGE_UNAVAILABLE, "网络超时")
        )

        strategy._on_kline_finished()

        assert position.pending_exit_order is not None, "查询失败必须保留出场单待重试"
        assert position.pending_exit_order.order_id == "POS1-x"
        assert trading.placed == []
        assert trading.canceled == []

    def test_tracking_is_idempotent(self):
        strategy, trading, _ = build_strategy()
        strategy.position_manager.add_position(build_position("POS1", 100.0, 100.0))
        set_latest_kline(strategy, 101.1)
        strategy._on_kline_finished()
        assert len(trading.placed) == 1
        position = strategy.position_manager.positions[0]

        trading.queue_query(
            OrderResult(
                custom_id=position.pending_exit_order.order_id,  # type: ignore[union-attr]
                symbol=SYMBOL,
                side=OrderSide.SELL,
                status=OrderStatus.OPEN,
                quantity=100.0,
                filled_qty=0.0,
            )
        )
        strategy._on_kline_finished()

        assert len(trading.placed) == 1, "已有出场单, 不得重复下单"

    def test_tracking_continues_after_pause(self):
        strategy, trading, _ = build_strategy()
        position = build_position("POS1", 100.0, 100.0)
        position.orders.append(make_exit("POS1-x", "POS1", OrderSide.SELL, 101.0))
        strategy.position_manager.add_position(position)
        strategy.is_running = False
        set_latest_kline(strategy, 101.1)
        trading.queue_query(
            OrderResult(
                custom_id="POS1-x",
                symbol=SYMBOL,
                side=OrderSide.SELL,
                status=OrderStatus.CLOSED,
                quantity=100.0,
                filled_qty=100.0,
            )
        )

        strategy._on_kline_finished()

        assert strategy.position_manager.positions == [], (
            "暂停后仍必须跟踪并归档已成交的出场单"
        )

    def test_pending_exit_survives_restart(self):
        journal = OrderJournal(":memory:")
        seed_position(
            journal,
            "POS1",
            exit_order=make_exit("POS1-x", "POS1", OrderSide.SELL, 101.0),
        )

        reloaded, trading2, _ = build_strategy(
            build_config(lowest_price=200.0), journal
        )
        restored = reloaded.position_manager.positions[0]
        assert restored.pending_exit_order is not None, "未成交的出场单必须随账本恢复"
        assert restored.pending_exit_order.order_id == "POS1-x"

        set_latest_kline(reloaded, 101.1)
        trading2.queue_query(
            OrderResult(
                custom_id="POS1-x",
                symbol=SYMBOL,
                side=OrderSide.SELL,
                status=OrderStatus.CLOSED,
                quantity=100.0,
                filled_qty=100.0,
            )
        )
        reloaded._on_kline_finished()

        assert reloaded.position_manager.positions == [], "重启后继续跟踪至归档"


class TestOrphanReaping:
    """孤儿回收: 空仓位超时后归档, 未超时保留作价格参考"""

    def test_flat_position_without_orders_is_reaped_after_timeout(self):
        journal = OrderJournal(":memory:")
        seed_position(
            journal,
            "POS1",
            entry_filled=0.0,
            entry_status=OrderStatus.EXPIRED,
            created_at="2026-10-01T00:00:00+00:00",
        )
        strategy, _, _ = build_strategy(
            build_config(lowest_price=200.0), journal
        )
        set_latest_kline(strategy, 100.0)

        strategy._on_kline_finished()

        assert strategy.position_manager.positions == []
        assert journal.load_active_positions(STRATEGY_KEY) == []

    def test_fresh_flat_position_is_kept(self):
        journal = OrderJournal(":memory:")
        seed_position(
            journal,
            "POS1",
            entry_filled=0.0,
            entry_status=OrderStatus.EXPIRED,
            created_at=datetime.now(UTC).isoformat(),
        )
        strategy, _, _ = build_strategy(
            build_config(lowest_price=200.0), journal
        )
        set_latest_kline(strategy, 100.0)

        strategy._on_kline_finished()

        assert [p.id for p in strategy.position_manager.positions] == ["POS1"], (
            "未超时的空仓位保留作网格价格参考"
        )

    def test_runtime_opened_position_carries_reference_price_and_created_at(self):
        strategy, _, _ = build_strategy(build_config(per_order_qty=0, lowest_price=0.0))
        set_latest_kline(strategy, 100.0)

        assert strategy.check_open_position() is True
        position = strategy.position_manager.positions[0]
        assert position.created_at != "", (
            "运行时开仓必须打上 created_at, 否则孤儿回收无法计时"
        )
        assert position.opening_price == pytest.approx(100.0)
        assert position.price == pytest.approx(100.0), (
            "空仓位以参考价参与网格间距 / 止盈判定"
        )

    def test_position_with_live_order_is_not_reaped(self):
        journal = OrderJournal(":memory:")
        seed_position(
            journal,
            "POS1",
            entry_filled=0.0,
            entry_status=OrderStatus.OPEN,
            created_at="2026-10-01T00:00:00+00:00",
        )
        strategy, _, _ = build_strategy(
            build_config(lowest_price=200.0), journal
        )
        set_latest_kline(strategy, 100.0)

        strategy._on_kline_finished()

        assert [p.id for p in strategy.position_manager.positions] == ["POS1"], (
            "存在存活订单的空仓位不是孤儿"
        )