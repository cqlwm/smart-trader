"""信号网格策略的配置与平仓结果契约"""

from pydantic import BaseModel, ConfigDict

from smart_trader.model import (
    OrderSide,
    PlaceOrderBehavior,
    PositionSide,
    SignalKind,
    Symbol,
)
from smart_trader.strategy.grid_position import GridPosition
from smart_trader.strategy.signal import Signal


class SignalGridStrategyConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    symbol: Symbol
    timeframe: str
    position_side: PositionSide = PositionSide.LONG
    master_side: OrderSide = OrderSide.BUY
    per_order_qty: float = 0.02
    # 加仓间距阈值, 正负号决定语义(见 GridSpacingGate):
    #   正数: 单向 —— 仅亏损方向走出间距后加仓
    #   负数: 双向距离限制 —— 距基准仓位不超过 |值| 即加仓(盈利方向也放行)
    grid_spacing_rate: float = 0.01
    # 顺势加仓间距: 非空时装配 TrendSpacingGate（以最外层仓位为基准, 盈利方向
    # 走出间距才加仓）, 并替换 grid_spacing_rate 的间距口径（二者语义互斥）
    trend_spacing_rate: float | None = None
    max_order: int = 10000
    highest_price: float = 1000000
    lowest_price: float = 0

    # 行情信号: 唯一的决策注入点, 闸门链与策略链共享同一实例
    signal: Signal | None = None
    # 信号种类: TRIGGER=无方向倾向(任何非中性信号都触发) / DIRECTIONAL=有方向倾向
    signal_kind: SignalKind = SignalKind.DIRECTIONAL

    # 启用退出信号（信号离场总开关）
    enable_exit_signal: bool = True
    # 逆势逐层减仓: 启用后信号离场改为"每根完成K线只减最外层一层"（亏损也减），
    # 替代信号止盈离场（enable_exit_signal 仍是总开关）
    enable_layered_reduce: bool = False
    # 退出信号最小止盈率
    exit_signal_take_profit_min_rate: float = 0.002

    # 固定比例止盈
    fixed_rate_take_profit: bool = False
    # 当fixed_rate_take_profit为True时, 是否使用限价单止盈
    # 开启会在入场订单完成之后创建止盈订单(默认情况下只有K线的收盘价达到止盈标准时才会触发止盈)
    take_profit_use_limit_order: bool = False
    # 固定比例止盈率
    fixed_take_profit_rate: float = 0.006

    place_order_behavior: PlaceOrderBehavior = PlaceOrderBehavior.CHASER_OPEN

    # 账本归属键: 多策略共享一个数据库, 按此键存取订单
    strategy_key: str

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
