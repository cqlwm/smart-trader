"""信号网格策略的配置与平仓结果契约"""

from pydantic import BaseModel, ConfigDict

from smart_trader.model import OrderSide, PlaceOrderBehavior, PositionSide, Symbol
from smart_trader.strategy import Signal
from smart_trader.strategy.grid_position import GridPosition


class SignalGridStrategyConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    symbol: Symbol
    timeframe: str
    position_side: PositionSide = PositionSide.LONG
    master_side: OrderSide = OrderSide.BUY
    per_order_qty: float = 0.02
    grid_spacing_rate: float = 0.01
    max_order: int = 10000
    highest_price: float = 1000000
    lowest_price: float = 0

    signal: Signal | None = None

    # 启用退出信号
    enable_exit_signal: bool = True
    # 退出信号最小止盈率
    exit_signal_take_profit_min_rate: float = 0.002

    # 固定比例止盈
    fixed_rate_take_profit: bool = False
    # 当fixed_rate_take_profit为True时, 是否使用限价单止盈
    # 开启会在入场订单完成之后创建止盈订单(默认情况下只有K线的收盘价达到止盈标准时才会触发止盈)
    take_profit_use_limit_order: bool = False
    # 固定比例止盈率
    fixed_take_profit_rate: float = 0.006

    place_order_behavior: PlaceOrderBehavior = (
        PlaceOrderBehavior.CHASER_OPEN
    )  # 下单行为

    # 账本归属键: 多策略共享一个数据库, 按此键存取订单
    strategy_key: str

    position_reverse: bool = False  # 是否反向持仓
    # 达到最大订单数全部止损
    enable_max_order_stop_loss: bool = False
    # 止损后暂停策略
    paused_after_stop_loss: bool = True
    # 单笔订单止损
    enable_order_stop_loss: bool = False
    order_stop_loss_rate: float = 0.05
    # 跟踪止损
    enable_trailing_stop: bool = False
    trailing_stop_rate: float = 0.02
    trailing_stop_activation_profit_rate: float = 0.01


class CloseOutcome(BaseModel):
    """一次平仓检查的结果

    @param archived 本根K线已到终态、需要归档的仓位
    @param acted 本根K线是否产生过平仓动作(挂出场单 / 撤单 / 归档);
        平仓优先于开仓 —— acted 为真时本根K线不再开仓
    """

    archived: list[GridPosition] = []
    acted: bool = False
