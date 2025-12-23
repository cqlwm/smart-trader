import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
import os

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model import OrderSide
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal


def create_mock_klines(num_points: int = 100) -> pd.DataFrame:
    """创建模拟K线数据用于测试"""
    base_time = datetime.now()
    data = []

    # 生成模拟价格数据
    np.random.seed(42)  # 固定随机种子以获得一致的结果
    base_price = 50000.0
    prices = []
    for i in range(num_points):
        # 随机游走价格
        change = np.random.normal(0, 100)  # 正态分布随机变动
        base_price += change
        base_price = max(base_price, 10000)  # 确保价格不低于10000
        prices.append(base_price)

    for i in range(num_points):
        dt = base_time + timedelta(minutes=i*5)  # 5分钟间隔
        price = prices[i]

        # 生成OHLCV数据
        high = price + abs(np.random.normal(0, 50))
        low = price - abs(np.random.normal(0, 50))
        open_price = prices[i-1] if i > 0 else price
        close = price
        volume = np.random.uniform(100, 1000)

        data.append({
            'datetime': dt.strftime('%Y-%m-%d %H:%M:%S'),
            'open': open_price,
            'high': high,
            'low': low,
            'close': close,
            'volume': volume,
            'finished': True
        })

    return pd.DataFrame(data)


def test_alpha_trend_signal_initialization():
    """测试AlphaTrendSignal初始化"""
    print("测试AlphaTrendSignal初始化...")

    # 测试买入信号初始化
    buy_signal = AlphaTrendSignal(OrderSide.BUY)
    assert buy_signal.side == OrderSide.BUY
    assert buy_signal.atr_multiple == 1.0
    assert buy_signal.period == 8
    assert buy_signal.reverse == False
    assert buy_signal.macd_fast_period == 12
    assert buy_signal.macd_slow_period == 26
    assert buy_signal.macd_signal_period == 9

    # 测试卖出信号初始化
    sell_signal = AlphaTrendSignal(OrderSide.SELL)
    assert sell_signal.side == OrderSide.SELL

    # 测试自定义参数
    custom_signal = AlphaTrendSignal(
        OrderSide.BUY,
        atr_multiple=2.0,
        period=14,
        reverse=True,
        macd_fast_period=8,
        macd_slow_period=21,
        macd_signal_period=5
    )
    assert custom_signal.atr_multiple == 2.0
    assert custom_signal.period == 14
    assert custom_signal.reverse == True
    assert custom_signal.macd_fast_period == 8
    assert custom_signal.macd_slow_period == 21
    assert custom_signal.macd_signal_period == 5

    print("✅ 初始化测试通过")


def test_alpha_trend_signal_run():
    """测试run方法"""
    print("测试run方法...")

    klines = create_mock_klines(50)  # 使用较少的数据点进行快速测试

    buy_signal = AlphaTrendSignal(OrderSide.BUY)
    sell_signal = AlphaTrendSignal(OrderSide.SELL)

    # 测试多次运行
    results_buy = []
    results_sell = []

    for i in range(20, len(klines)):
        current_data = klines.iloc[:i+1].copy()
        buy_result = buy_signal.run(current_data)
        sell_result = sell_signal.run(current_data)

        results_buy.append(buy_result)
        results_sell.append(sell_result)

        # 验证返回值类型和范围
        assert isinstance(buy_result, int)
        assert buy_result in [-1, 0, 1]
        assert isinstance(sell_result, int)
        assert sell_result in [-1, 0, 1]

    # 确保产生了至少一些信号（非零值）
    assert any(r != 0 for r in results_buy), "买入信号应该产生一些非零信号"
    assert any(r != 0 for r in results_sell), "卖出信号应该产生一些非零信号"

    print(f"✅ run方法测试通过，产生了{sum(1 for r in results_buy if r != 0)}个买入信号，{sum(1 for r in results_sell if r != 0)}个卖出信号")


def test_alpha_trend_signal_is_entry_exit():
    """测试is_entry和is_exit方法"""
    print("测试is_entry和is_exit方法...")

    klines = create_mock_klines(50)

    buy_signal = AlphaTrendSignal(OrderSide.BUY)
    sell_signal = AlphaTrendSignal(OrderSide.SELL)

    # 测试entry/exit逻辑
    for i in range(20, len(klines)):
        current_data = klines.iloc[:i+1].copy()

        # 测试买入信号的entry/exit
        buy_is_entry = buy_signal.is_entry(current_data)
        buy_is_exit = buy_signal.is_exit(current_data)
        buy_run_result = buy_signal.run(current_data)

        # 测试卖出信号的entry/exit
        sell_is_entry = sell_signal.is_entry(current_data)
        sell_is_exit = sell_signal.is_exit(current_data)
        sell_run_result = sell_signal.run(current_data)

        # 验证返回值类型
        assert isinstance(buy_is_entry, bool)
        assert isinstance(buy_is_exit, bool)
        assert isinstance(sell_is_entry, bool)
        assert isinstance(sell_is_exit, bool)

        # 验证逻辑一致性
        assert buy_is_entry == (buy_run_result == 1)
        assert buy_is_exit == (buy_run_result == -1)
        assert sell_is_entry == (sell_run_result == -1)
        assert sell_is_exit == (sell_run_result == 1)

    print("✅ is_entry/is_exit方法测试通过")


def test_alpha_trend_signal_golden_dead_cross():
    """测试golden_cross和dead_cross方法"""
    print("测试golden_cross和dead_cross方法...")

    klines = create_mock_klines(100)

    signal = AlphaTrendSignal(OrderSide.BUY)

    # 运行信号生成MACD数据
    golden_cross_count = 0
    dead_cross_count = 0

    for i in range(30, len(klines)):
        current_data = klines.iloc[:i+1].copy()
        signal.run(current_data)

        golden = signal.golden_cross()
        dead = signal.dead_cross()

        if golden:
            golden_cross_count += 1
        if dead:
            dead_cross_count += 1

        # 验证返回值类型
        assert isinstance(golden, bool)
        assert isinstance(dead, bool)

        # 金叉和死叉不应该同时发生
        assert not (golden and dead), "金叉和死叉不应该同时发生"

    print(f"✅ golden_cross/dead_cross方法测试通过，发现{golden_cross_count}个金叉，{dead_cross_count}个死叉")


def test_alpha_trend_signal_reverse():
    """测试reverse参数"""
    print("测试reverse参数...")

    klines = create_mock_klines(50)

    normal_signal = AlphaTrendSignal(OrderSide.BUY, reverse=False)
    reverse_signal = AlphaTrendSignal(OrderSide.BUY, reverse=True)

    # 比较正常和反转信号的结果
    normal_results = []
    reverse_results = []

    for i in range(20, len(klines)):
        current_data = klines.iloc[:i+1].copy()

        normal_result = normal_signal.run(current_data)
        reverse_result = reverse_signal.run(current_data)

        normal_results.append(normal_result)
        reverse_results.append(reverse_result)

        # 反转信号应该与正常信号相反（除了0值）
        if normal_result != 0:
            assert reverse_result == -normal_result, f"反转信号应该与正常信号相反: {normal_result} vs {reverse_result}"

    print("✅ reverse参数测试通过")


def test_alpha_trend_signal_edge_cases():
    """测试边界情况"""
    print("测试边界情况...")

    # 测试空数据
    buy_signal = AlphaTrendSignal(OrderSide.BUY)
    empty_df = pd.DataFrame(columns=['datetime', 'open', 'high', 'low', 'close', 'volume', 'finished'])

    try:
        result = buy_signal.run(empty_df)
        # 空数据应该返回0或抛出合理异常
        assert result == 0 or isinstance(result, (int, type(None)))
    except Exception as e:
        # 如果抛出异常，应该是有意义的异常
        assert "empty" in str(e).lower() or "insufficient" in str(e).lower()

    # 测试单行数据
    single_row_df = create_mock_klines(1)
    result = buy_signal.run(single_row_df)
    assert isinstance(result, int)
    assert result in [-1, 0, 1]

    print("✅ 边界情况测试通过")


if __name__ == "__main__":
    print("=== AlphaTrendSignal 单元测试 ===\n")

    try:
        test_alpha_trend_signal_initialization()
        test_alpha_trend_signal_run()
        test_alpha_trend_signal_is_entry_exit()
        test_alpha_trend_signal_golden_dead_cross()
        test_alpha_trend_signal_reverse()
        test_alpha_trend_signal_edge_cases()

        print("\n🎉 所有单元测试通过！AlphaTrendSignal的各个方法工作正常。")

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
