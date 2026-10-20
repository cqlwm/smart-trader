"""入场闸门链测试

覆盖: 各闸门规则 / 短路与顺序 / 隔离机制 / 空值与边界。
判定口径（与既有实现逐字一致）：
- `PriceRangeGate` 闭区间（含等号）
- `GridSpacingGate` 无仓位放行，间距判定 `<=` 含等号
- `TrendSpacingGate` 无仓位放行；只有一层已成交仓位时第二层无条件放行；
  两层及以上走顺势间距 `>=`（含等号，基准为最外层）或回踩带
  （按价格高低取最外层两层，收盘价落在其开仓价闭区间内）
"""

import pytest

from smart_trader.model import (
    OrderSide,
    PositionLimit,
    PositionSide,
    SignalDirection,
)
from smart_trader.strategy.entry_gate import (
    DirectionalSignalGate,
    EntryContext,
    EntryGateChain,
    GridSpacingGate,
    MaxOrderStopLossGate,
    MaxPositionsGate,
    PriceRangeGate,
    SignalTriggerGate,
    TrendSpacingGate,
)
from tests.fakes import (
    RecordingGate,
    make_entry_context,
    make_market_signal,
    make_position,
)

# ---- MaxPositionsGate ----


def test_max_positions_gate_allows_below_limit():
    """仓位数未达上限时放行"""
    gate = MaxPositionsGate(PositionLimit(max_order=3))

    assert gate.allow(make_entry_context()) is True, "空仓时必须允许开仓"


def test_max_positions_gate_blocks_at_limit():
    """仓位数恰好等于上限时拦截"""
    gate = MaxPositionsGate(PositionLimit(max_order=2))
    ctx = make_entry_context([make_position("P1"), make_position("P2")])

    assert gate.allow(ctx) is False, "满仓时必须拦截"


# ---- SignalTriggerGate ----


def test_signal_trigger_gate_allows_when_signal_is_none():
    """未配置信号时放行（既有语义: 无信号 = 不限制）"""
    gate = SignalTriggerGate()

    assert gate.allow(make_entry_context(market_signal=None)) is True, (
        "空信号应放行, 不能等同于中性信号"
    )


def test_signal_trigger_gate_allows_on_any_non_neutral_direction():
    """任何非中性方向都触发（无方向倾向）"""
    gate = SignalTriggerGate()

    for direction in (SignalDirection.LONG, SignalDirection.SHORT):
        ctx = make_entry_context(market_signal=make_market_signal(direction))
        assert gate.allow(ctx) is True, f"{direction.value} 应触发入场"


def test_signal_trigger_gate_blocks_on_neutral():
    """中性信号不触发"""
    gate = SignalTriggerGate()
    ctx = make_entry_context(
        market_signal=make_market_signal(SignalDirection.NEUTRAL)
    )

    assert gate.allow(ctx) is False, "中性信号不应触发入场"


# ---- DirectionalSignalGate ----


def test_directional_gate_allows_matching_direction():
    """做多仓位: 看涨信号许可入场"""
    gate = DirectionalSignalGate(OrderSide.BUY)
    ctx = make_entry_context(market_signal=make_market_signal(SignalDirection.LONG))

    assert gate.allow(ctx) is True, "看涨信号应许可买入入场"


def test_directional_gate_blocks_opposite_direction():
    """做多仓位: 看跌信号不许可入场"""
    gate = DirectionalSignalGate(OrderSide.BUY)
    ctx = make_entry_context(market_signal=make_market_signal(SignalDirection.SHORT))

    assert gate.allow(ctx) is False, "反向信号不应许可入场"


def test_directional_gate_blocks_neutral():
    """中性信号不许可入场"""
    gate = DirectionalSignalGate(OrderSide.SELL)
    ctx = make_entry_context(
        market_signal=make_market_signal(SignalDirection.NEUTRAL)
    )

    assert gate.allow(ctx) is False, "中性信号不应许可入场"


def test_directional_gate_allows_short_position_on_short_signal():
    """做空仓位: 看跌信号许可入场"""
    gate = DirectionalSignalGate(OrderSide.SELL)
    ctx = make_entry_context(market_signal=make_market_signal(SignalDirection.SHORT))

    assert gate.allow(ctx) is True, "看跌信号应许可卖出入场"


def test_directional_gate_allows_when_signal_is_none():
    """未配置信号时放行"""
    gate = DirectionalSignalGate(OrderSide.BUY)

    assert gate.allow(make_entry_context(market_signal=None)) is True, (
        "空信号应放行"
    )


# ---- PriceRangeGate ----


def test_price_range_gate_allows_inside_range():
    """价格在区间内放行"""
    gate = PriceRangeGate(0.4, 0.6)

    assert gate.allow(make_entry_context(close=0.5)) is True


def test_price_range_gate_allows_at_lower_bound():
    """恰好等于下界放行（闭区间）"""
    gate = PriceRangeGate(0.4, 0.6)

    assert gate.allow(make_entry_context(close=0.4)) is True, "下界是闭区间端点"


def test_price_range_gate_allows_at_upper_bound():
    """恰好等于上界放行（闭区间）"""
    gate = PriceRangeGate(0.4, 0.6)

    assert gate.allow(make_entry_context(close=0.6)) is True, "上界是闭区间端点"


def test_price_range_gate_blocks_outside_range():
    """价格越界拦截"""
    gate = PriceRangeGate(0.4, 0.6)

    assert gate.allow(make_entry_context(close=0.7)) is False, "高于上界应拦截"
    assert gate.allow(make_entry_context(close=0.3)) is False, "低于下界应拦截"


def test_price_range_gate_blocks_when_close_is_none():
    """尚无行情时拦截（对应 latest_kline is None）"""
    gate = PriceRangeGate(0.4, 0.6)

    assert gate.allow(make_entry_context(close=None)) is False, (
        "无收盘价时不能判定价格区间"
    )


# ---- GridSpacingGate ----


def test_grid_spacing_gate_allows_when_no_positions():
    """无仓位时直接放行（第一单不受间距约束）"""
    gate = GridSpacingGate(0.01, OrderSide.BUY)

    assert gate.allow(make_entry_context()) is True, "空仓时应放行第一单"


def test_grid_spacing_gate_blocks_inside_spacing():
    """价格未走到反向间距时不加仓"""
    gate = GridSpacingGate(0.01, OrderSide.BUY)
    ctx = make_entry_context(
        [make_position("P1", opening_price=0.5, opening_quantity=100)],
        close=0.4995,
    )

    assert gate.allow(ctx) is False, "跌幅未达 1% 间距, 不应加仓"


def test_grid_spacing_gate_allows_exactly_at_spacing_rate():
    """恰好等于间距时放行（<= 含等号）"""
    gate = GridSpacingGate(0.01, OrderSide.BUY)
    ctx = make_entry_context(
        [make_position("P1", opening_price=0.5, opening_quantity=100)], close=0.495
    )

    assert gate.allow(ctx) is True, "恰好等于间距时应放行"


def test_grid_spacing_gate_picks_lowest_price_for_buy_master_side():
    """做多主方向: 取价格最低的仓位作为间距基准"""
    gate = GridSpacingGate(0.01, OrderSide.BUY)
    positions = [
        make_position("HIGH", opening_price=0.6, opening_quantity=100),
        make_position("LOW", opening_price=0.5, opening_quantity=100),
    ]

    # 相对最低价 0.5 跌幅 1% -> 放行（若误取最高价 0.6 则不会放行）
    ctx = make_entry_context(positions, close=0.495)
    assert gate.allow(ctx) is True, "做多主方向应取最低价仓位为基准"


# ---- GridSpacingGate: 负间距 = 双向距离限制 ----


def test_negative_spacing_allows_within_distance_in_profit_direction():
    """负间距: 逆势上涨但幅度在阈值内也放行（双向距离限制）

    `grid_spacing_rate` 为负时判定变为 `ratio <= |rate|`, 因此盈利方向同样
    会被放行 —— 这不是"顺下跌加仓", 而是"距基准价不超过阈值就加仓"。
    """
    gate = GridSpacingGate(-0.1, OrderSide.BUY)
    positions = [make_position("P1", opening_price=0.5, opening_quantity=100)]

    # 相对 0.5 上涨 2% -> 逆势但仍在 10% 距离内
    ctx = make_entry_context(positions, close=0.51)
    assert gate.allow(ctx) is True, "负间距下盈利方向未超阈值应放行"


def test_negative_spacing_allows_exactly_at_distance():
    """负间距: 恰好等于距离阈值时放行（<= 含等号）"""
    gate = GridSpacingGate(-0.1, OrderSide.BUY)
    positions = [make_position("P1", opening_price=0.5, opening_quantity=100)]

    ctx = make_entry_context(positions, close=0.55)

    assert gate.allow(ctx) is True, "恰好等于 10% 距离时应放行"


def test_negative_spacing_blocks_beyond_distance():
    """负间距: 超出距离阈值后拦截"""
    gate = GridSpacingGate(-0.1, OrderSide.BUY)
    positions = [make_position("P1", opening_price=0.5, opening_quantity=100)]

    ctx = make_entry_context(positions, close=0.60)

    assert gate.allow(ctx) is False, "盈利 20% 已超出 10% 距离, 应拦截"


def test_negative_spacing_also_allows_loss_direction():
    """负间距: 亏损方向同样放行（与正值的单向限制不同）"""
    gate = GridSpacingGate(-0.1, OrderSide.BUY)
    positions = [make_position("P1", opening_price=0.5, opening_quantity=100)]

    ctx = make_entry_context(positions, close=0.45)

    assert gate.allow(ctx) is True, "负间距下亏损 10% 恰好在阈值内, 应放行"


def test_positive_spacing_blocks_profit_direction():
    """正间距是单向限制: 盈利方向一律拦截（与负值的关键差异）"""
    gate = GridSpacingGate(0.1, OrderSide.BUY)
    positions = [make_position("P1", opening_price=0.5, opening_quantity=100)]

    ctx = make_entry_context(positions, close=0.51)

    assert gate.allow(ctx) is False, "正间距下盈利方向不得加仓"


def test_grid_spacing_gate_picks_highest_price_for_sell_master_side():
    """做空主方向: 取价格最高的仓位作为间距基准

    注意: 间距的盈亏方向由仓位自己的 `entry_side` 决定（`profit_and_loss_ratio`）,
    极值基准的选取才由 `master_side` 决定 —— 因此空头仓位 + SELL 主方向才构成
    "价格上涨走出间距"的场景。
    """
    gate = GridSpacingGate(0.01, OrderSide.SELL)
    positions = [
        make_position(
            "LOW", position_side=PositionSide.SHORT, opening_price=0.5, opening_quantity=100
        ),
        make_position(
            "HIGH", position_side=PositionSide.SHORT, opening_price=0.6, opening_quantity=100
        ),
    ]

    # 相对最高价 0.6 上涨 1% -> 放行（若误取最低价 0.5 则不会放行）
    ctx = make_entry_context(positions, close=0.606)
    assert gate.allow(ctx) is True, "做空主方向应取最高价仓位为基准"


def test_grid_spacing_gate_uses_position_quantity_for_reference_price():
    """无成交仓位以 opening_price 为基准（opening_quantity=0 时不参与加权）"""
    gate = GridSpacingGate(0.01, OrderSide.BUY)
    positions = [make_position("P1", opening_price=0.5, opening_quantity=0.0)]

    ctx = make_entry_context(positions, close=0.495)
    assert gate.allow(ctx) is True, "空仓位仍应提供网格参考价"


# ---- TrendSpacingGate: 顺势阶梯加仓 ----


def test_trend_spacing_gate_allows_when_no_positions():
    """无仓位时直接放行（第一单不受间距约束）"""
    gate = TrendSpacingGate(0.01, OrderSide.BUY)

    assert gate.allow(make_entry_context()) is True, "空仓时应放行第一单"


def test_trend_spacing_gate_blocks_inside_spacing():
    """两层仓位: 盈利方向未走出间距且不在回踩带内时不加仓"""
    gate = TrendSpacingGate(0.01, OrderSide.BUY)
    positions = [
        make_position("P1", opening_price=0.5, opening_quantity=100),
        make_position("P2", opening_price=0.505, opening_quantity=100),
    ]
    ctx = make_entry_context(positions, close=0.507)

    assert gate.allow(ctx) is False, "上涨 0.4% 未达 1% 间距, 不应顺势加仓"


def test_trend_spacing_gate_allows_exactly_at_spacing_rate():
    """恰好等于间距时放行（>= 含等号）"""
    gate = TrendSpacingGate(0.01, OrderSide.BUY)
    positions = [
        make_position("P1", opening_price=0.5, opening_quantity=100),
        make_position("P2", opening_price=0.52, opening_quantity=100),
    ]
    ctx = make_entry_context(positions, close=0.5252)

    assert gate.allow(ctx) is True, "恰好等于间距时应放行"


def test_trend_spacing_gate_allows_beyond_spacing():
    """盈利方向走出间距后放行"""
    gate = TrendSpacingGate(0.01, OrderSide.BUY)
    positions = [
        make_position("P1", opening_price=0.5, opening_quantity=100),
        make_position("P2", opening_price=0.52, opening_quantity=100),
    ]
    ctx = make_entry_context(positions, close=0.55)

    assert gate.allow(ctx) is True, "上涨 5.8% 走出 1% 间距, 应放行"


def test_trend_spacing_gate_blocks_loss_direction():
    """两层仓位: 亏损方向且不在回踩带内时拦截"""
    gate = TrendSpacingGate(0.01, OrderSide.BUY)
    positions = [
        make_position("LOW", opening_price=0.5, opening_quantity=100),
        make_position("HIGH", opening_price=0.6, opening_quantity=100),
    ]
    ctx = make_entry_context(positions, close=0.49)

    assert gate.allow(ctx) is False, "顺势闸门不得在亏损方向加仓"


def test_trend_spacing_gate_blocks_when_close_is_none():
    """尚无行情时拦截"""
    gate = TrendSpacingGate(0.01, OrderSide.BUY)
    ctx = make_entry_context(
        [make_position("P1", opening_price=0.5, opening_quantity=100)], close=None
    )

    assert gate.allow(ctx) is False, "无收盘价时不能判定顺势间距"


def test_trend_spacing_gate_allows_second_layer_in_either_direction():
    """只有一层已成交仓位时, 第二层无论逆势还是顺势都放行"""
    gate = TrendSpacingGate(0.01, OrderSide.BUY)
    positions = [make_position("P1", opening_price=100.0, opening_quantity=1.0)]

    for close in (99.0, 101.0):
        ctx = make_entry_context(positions, close=close)
        assert gate.allow(ctx) is True, f"第二层 {close} 应无条件加仓"

    short_gate = TrendSpacingGate(0.01, OrderSide.SELL)
    short_positions = [
        make_position(
            "S1",
            position_side=PositionSide.SHORT,
            opening_price=100.0,
            opening_quantity=1.0,
        )
    ]
    for close in (99.0, 101.0):
        ctx = make_entry_context(short_positions, close=close)
        assert short_gate.allow(ctx) is True, f"空头第二层 {close} 应无条件加仓"


def test_trend_spacing_gate_uses_outermost_layer_as_benchmark():
    """做多: 取价格最高的仓位作为基准（最外层）

    0.602 在回踩带 0.5..0.6 之外; 若误取最低价层, 相对 0.5 已 +20% 必然放行,
    但正确的基准是最外层 0.6, +0.33% 未达 1% 间距, 必须拦截。
    """
    gate = TrendSpacingGate(0.01, OrderSide.BUY)
    positions = [
        make_position("LOW", opening_price=0.5, opening_quantity=100),
        make_position("HIGH", opening_price=0.6, opening_quantity=100),
    ]

    allowed = make_entry_context(positions, close=0.606)
    assert gate.allow(allowed) is True, "相对最外层上涨 1% 应放行"

    blocked = make_entry_context(positions, close=0.602)
    assert gate.allow(blocked) is False, "相对最外层未走出间距时应拦截"


def test_trend_spacing_gate_short_side_uses_lowest_price_layer():
    """做空: 基准取最低价层, 价格顺盈利方向下跌走出间距才放行"""
    gate = TrendSpacingGate(0.01, OrderSide.SELL)
    positions = [
        make_position(
            "HIGH",
            position_side=PositionSide.SHORT,
            opening_price=0.6,
            opening_quantity=100,
        ),
        make_position(
            "LOW",
            position_side=PositionSide.SHORT,
            opening_price=0.5,
            opening_quantity=100,
        ),
    ]

    falling = make_entry_context(positions, close=0.495)
    assert gate.allow(falling) is True, "做空应取最低价层为基准, 顺下跌方向放行"

    rising = make_entry_context(positions, close=0.606)
    assert gate.allow(rising) is False, "做空不得在上涨方向加仓"


def test_trend_spacing_gate_allows_add_inside_outermost_two_entry_prices():
    """回踩带: 收盘价落在最外层两层开仓价之间时加仓（示例 103..106 + 105）"""
    gate = TrendSpacingGate(0.0001, OrderSide.BUY)
    positions = [
        make_position("P100", opening_price=100.0, opening_quantity=1.0),
        make_position("P101", opening_price=101.0, opening_quantity=1.0),
        make_position("P103", opening_price=103.0, opening_quantity=1.0),
        make_position("P106", opening_price=106.0, opening_quantity=1.0),
    ]

    # 105 未越过最外层 106 的 0.01% 间距, 但位于最外层两层 103..106 之间
    ctx = make_entry_context(positions, close=105.0)
    assert gate.allow(ctx) is True, "小回踩不破最外层两层开仓价时应视为加仓成功"


def test_trend_spacing_gate_blocks_below_outermost_two_entry_prices():
    """回踩带: 收盘价跌破最外层两层开仓价下沿时不加仓"""
    gate = TrendSpacingGate(0.0001, OrderSide.BUY)
    positions = [
        make_position("P100", opening_price=100.0, opening_quantity=1.0),
        make_position("P101", opening_price=101.0, opening_quantity=1.0),
        make_position("P103", opening_price=103.0, opening_quantity=1.0),
        make_position("P106", opening_price=106.0, opening_quantity=1.0),
    ]

    ctx = make_entry_context(positions, close=102.0)
    assert gate.allow(ctx) is False, "102 在 103..106 之外且未走出顺势间距, 应拦截"


def test_trend_spacing_gate_band_uses_two_outermost_prices():
    """回踩带按价格高低取最外层两层, 不是按创建顺序取最近两层

    构造: 100 -> 120 -> 105 -> 106; 最外层两层是 120/106（区间 106..120）,
    而最近两层（创建顺序）是 105/106（区间 105..106）。
    110 只落在价格口径区间内, 105.5 只落在创建顺序口径区间内。
    """
    gate = TrendSpacingGate(0.0001, OrderSide.BUY)
    positions = [
        make_position("P100", opening_price=100.0, opening_quantity=1.0),
        make_position("P120", opening_price=120.0, opening_quantity=1.0),
        make_position("P105", opening_price=105.0, opening_quantity=1.0),
        make_position("P106", opening_price=106.0, opening_quantity=1.0),
    ]

    inside_price_band = make_entry_context(positions, close=110.0)
    assert gate.allow(inside_price_band) is True, (
        "110 在最外层两层 106..120 之间, 应放行"
    )

    inside_creation_band = make_entry_context(positions, close=105.5)
    assert gate.allow(inside_creation_band) is False, (
        "105.5 只在创建顺序的 105..106 内, 不应按创建顺序放行"
    )


def test_trend_spacing_gate_band_is_inclusive():
    """回踩带边界: 恰好等于最外层两层开仓价时放行（闭区间）"""
    gate = TrendSpacingGate(0.0001, OrderSide.BUY)
    positions = [
        make_position("P103", opening_price=103.0, opening_quantity=1.0),
        make_position("P106", opening_price=106.0, opening_quantity=1.0),
    ]

    for close in (103.0, 106.0):
        ctx = make_entry_context(positions, close=close)
        assert gate.allow(ctx) is True, f"{close} 位于闭区间端点时应放行"


def test_trend_spacing_gate_band_ignores_empty_layers():
    """回踩带只统计已成交仓位: 零数量层不能占据最外层两层名额"""
    gate = TrendSpacingGate(0.0001, OrderSide.BUY)
    positions = [
        make_position("FILLED1", opening_price=103.0, opening_quantity=1.0),
        make_position("FILLED2", opening_price=106.0, opening_quantity=1.0),
        make_position("EMPTY", opening_price=120.0, opening_quantity=0.0),
    ]

    ctx = make_entry_context(positions, close=115.0)
    assert gate.allow(ctx) is False, (
        "已成交层最外层两层是 103..106, 115 不在其中; 零数量 120 不得参与回踩带"
    )


def test_trend_spacing_gate_short_side_band():
    """空头对称: 收盘价落在最外层两层开仓价之间也加仓"""
    gate = TrendSpacingGate(0.0001, OrderSide.SELL)
    positions = [
        make_position(
            "P106",
            position_side=PositionSide.SHORT,
            opening_price=106.0,
            opening_quantity=1.0,
        ),
        make_position(
            "P103",
            position_side=PositionSide.SHORT,
            opening_price=103.0,
            opening_quantity=1.0,
        ),
    ]

    ctx = make_entry_context(positions, close=105.0)
    assert gate.allow(ctx) is True, "空头回踩带与多头对称"


# ---- MaxOrderStopLossGate ----


def test_max_order_stop_loss_gate_allows_when_disabled():
    """未启用上限止损时永不拦截"""
    gate = MaxOrderStopLossGate(PositionLimit(max_order=2, enable_stop_loss=False))

    assert gate.allow(make_entry_context()) is True, "功能未开启时不应拦截"


def test_max_order_stop_loss_gate_blocks_at_last_slot():
    """只剩 1 个空位时拦截（上限止损已触发, 全部平仓中不再开新仓）"""
    gate = MaxOrderStopLossGate(PositionLimit(max_order=2, enable_stop_loss=True))
    ctx = make_entry_context([make_position("P1")])

    assert gate.allow(ctx) is False, "触发上限止损后不应再开仓"


def test_max_order_stop_loss_gate_allows_with_more_slots():
    """还有 2 个空位时放行"""
    gate = MaxOrderStopLossGate(PositionLimit(max_order=10, enable_stop_loss=True))
    ctx = make_entry_context([make_position("P1")])

    assert gate.allow(ctx) is True


def test_max_order_stop_loss_gate_allows_first_slot_when_max_order_is_one():
    """max_order=1 空仓时放行: 上限止损不适用, 允许开唯一仓位"""
    gate = MaxOrderStopLossGate(PositionLimit(max_order=1, enable_stop_loss=True))

    assert gate.allow(make_entry_context()) is True, (
        "容量为 1 不存在\"最后一格\"状态, 空仓应放行第一单"
    )


# ---- EntryGateChain ----


def test_gate_chain_allows_when_all_gates_allow():
    """全部闸门放行则整体放行"""
    chain = EntryGateChain([RecordingGate(True, "a"), RecordingGate(True, "b")])

    assert chain.allows(make_entry_context()) is True


def test_gate_chain_blocks_on_first_failing_gate():
    """任一闸门拦截则整体拦截"""
    chain = EntryGateChain([RecordingGate(True, "a"), RecordingGate(False, "b")])

    assert chain.allows(make_entry_context()) is False


def test_gate_chain_short_circuits_after_first_failure():
    """短路：首个闸门拦截后不再评估后续闸门"""
    gates = [RecordingGate(False, "first"), RecordingGate(True, "second")]
    chain = EntryGateChain(gates)

    chain.allows(make_entry_context())

    assert gates[1].calls == [], "首个闸门拦截后不应再评估后续闸门"


def test_gate_chain_evaluates_all_gates_when_all_pass():
    """全部放行时按声明顺序评估每个闸门"""
    gates = [RecordingGate(True, "first"), RecordingGate(True, "second")]
    chain = EntryGateChain(gates)

    chain.allows(make_entry_context())

    assert [gates[0].calls, gates[1].calls] == [["first"], ["second"]], (
        "全部放行时应按声明顺序评估"
    )


def test_empty_gate_chain_always_allows():
    """空链恒放行（无闸门即无限制）"""
    assert EntryGateChain([]).allows(make_entry_context()) is True


def test_two_gate_chains_do_not_share_gate_instances():
    """隔离: 两条链的调用记录互不干扰"""
    gate_a = RecordingGate(True, "a")
    gate_b = RecordingGate(True, "b")
    chain_a = EntryGateChain([gate_a])
    chain_b = EntryGateChain([gate_b])

    chain_a.allows(make_entry_context())
    chain_a.allows(make_entry_context())
    chain_b.allows(make_entry_context())

    assert len(gate_a.calls) == 2, "链 A 应记录 2 次"
    assert len(gate_b.calls) == 1, "链 B 应只记录自己的 1 次"


def test_gate_chain_preserves_declared_gate_order():
    """顺序: 闸门按声明顺序评估, 不被重排"""
    gates = [RecordingGate(True, n) for n in ("a", "b", "c")]
    chain = EntryGateChain(gates)

    chain.allows(make_entry_context())

    assert [c for g in gates for c in g.calls] == ["a", "b", "c"]


# ---- EntryContext ----


def test_entry_context_defaults_to_no_positions_and_no_close():
    """默认值: 空仓位 + 无行情 + 无信号"""
    ctx = EntryContext()

    assert ctx.positions == [], "默认无仓位"
    assert ctx.close is None, "默认无收盘价"
    assert ctx.market_signal is None, "默认无信号"


def test_require_close_returns_value_when_present():
    """有收盘价时原样返回"""
    ctx = make_entry_context(close=0.5)

    assert ctx.require_close() == 0.5


def test_require_close_raises_when_close_is_none():
    """无收盘价时抛 SmartTraderError（不用 assert, python -O 下会被剥离）"""
    from smart_trader.exceptions import SmartTraderError

    ctx = EntryContext(close=None)

    with pytest.raises(SmartTraderError):
        ctx.require_close()


def test_entry_context_keeps_position_identity():
    """上下文按引用透传仓位（深拷贝会丢 trailing_stop_price 可见性）"""
    position = make_position("P1", trailing_stop_price=0.45)
    ctx = make_entry_context([position])

    assert ctx.positions[0] is position, "必须是同一对象而非副本"
    assert ctx.positions[0].trailing_stop_price == 0.45, (
        "trailing_stop_price 的运行时修改必须对闸门可见"
    )


def test_entry_context_rejects_unknown_field():
    """extra=forbid: 字段拼写错误立即报错"""
    with pytest.raises(ValueError):
        EntryContext.model_validate({"close": 0.5, "signal": None})


def test_entry_context_accepts_market_signal_field():
    """行情信号字段名为 market_signal"""
    signal = make_market_signal(SignalDirection.SHORT)
    ctx = make_entry_context(market_signal=signal)

    assert ctx.market_signal is signal, "行情信号应被正确持有"
    assert signal.direction is SignalDirection.SHORT, "方向应原样透传"