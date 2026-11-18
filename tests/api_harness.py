"""只读 API 测试装配: 账本播种工具与固定常量"""

from collections.abc import Sequence

from smart_trader.model import (
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.storage.records import GridPositionRow, OrderRecord

DEFAULT_STRATEGY_KEY = "dogeusdt_long_buy_1m"
OTHER_STRATEGY_KEY = "zecusdt_short_sell_1m"
DEFAULT_SYMBOL = "DOGE/USDT:USDT"
OTHER_SYMBOL = "ZEC/USDT:USDT"

# API 强制鉴权后, 所有测试客户端默认携带该 token
TEST_TOKEN = "test-token"
AUTH_HEADERS: dict[str, str] = {"Authorization": f"Bearer {TEST_TOKEN}"}


def make_position(
    position_id: str = "POS1",
    *,
    strategy_key: str = DEFAULT_STRATEGY_KEY,
    symbol: str = DEFAULT_SYMBOL,
    position_side: PositionSide = PositionSide.LONG,
    main_order_side: OrderSide = OrderSide.BUY,
    **overrides: object,
) -> GridPositionRow:
    """构造仓位行; 额外字段经 overrides 注入"""
    fields: dict[str, object] = {
        "id": position_id,
        "strategy_key": strategy_key,
        "symbol": symbol,
        "position_side": position_side,
        "main_order_side": main_order_side,
        "fixed_take_profit_rate": 0.01,
        "signal_min_take_profit_rate": 0.002,
    }
    fields.update(overrides)
    return GridPositionRow.model_validate(fields)


def make_order(
    order_id: str,
    position_id: str,
    *,
    strategy_key: str = DEFAULT_STRATEGY_KEY,
    symbol: str = DEFAULT_SYMBOL,
    side: OrderSide = OrderSide.BUY,
    status: OrderStatus | None = OrderStatus.OPEN,
    price: float | None = 0.5,
    quantity: float = 10.0,
    filled_qty: float = 0.0,
    avg_price: float | None = None,
    created_at: str = "",
) -> OrderRecord:
    """构造订单行; status=None 表示结果未确认（WAL）"""
    return OrderRecord(
        order_id=order_id,
        grid_position_id=position_id,
        strategy_key=strategy_key,
        symbol=symbol,
        side=side,
        type=OrderType.LIMIT,
        price=price,
        quantity=quantity,
        filled_qty=filled_qty,
        avg_price=avg_price,
        status=status,
        created_at=created_at,
    )


def seed_position(
    journal: OrderJournal,
    position: GridPositionRow,
    orders: Sequence[OrderRecord],
) -> None:
    """写入一个仓位及其订单序列（首单与仓位同事务）"""
    first = orders[0]
    journal.open_position(position, first)
    for order in orders[1:]:
        journal.append_order(order)
