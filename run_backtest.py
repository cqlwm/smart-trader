#!/usr/bin/env python3
"""
回测运行脚本示例
演示如何使用回测系统运行策略
"""

import os
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from model import Symbol, OrderSide, PositionSide
from backtest.data_loader import HistoricalDataLoader
from backtest.backtest_client import BacktestClient
from backtest.backtest_event_loop import BacktestEventLoop
from backtest.analyzer import BacktestAnalyzer
from task.backtest_task import BacktestTask
from strategy.grids_strategy_v2 import SignalGridStrategy, SignalGridStrategyConfig
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal
from config import DATA_PATH
import log

logger = log.getLogger(__name__)


def run_backtest_example(
    symbol: Symbol = Symbol(base="ETH", quote="USDT"),
    timeframe: str = "1m",
    start_time: str = "2025-10-01",
    end_time: str = "2025-10-31",
    data_dir: str = "data",
    initial_balance: float = 10000.0,
    strategy_config: SignalGridStrategyConfig | None = None,
):
    try:
        # 1. 加载历史数据（自动下载并缓存）
        logger.info("加载历史数据...")
        data_loader = HistoricalDataLoader()
        file_path = data_loader.ensure_data(symbol, timeframe, start_time, end_time, data_dir)
        historical_klines = data_loader.load_csv(file_path, symbol, timeframe)

        if not historical_klines:
            logger.error("未加载到历史数据")
            return

        logger.info(f"加载了 {len(historical_klines)} 根K线数据")

        # 2. 创建回测客户端
        backtest_client = BacktestClient(
            initial_balance=initial_balance,
            maker_fee=0.0002,
            taker_fee=0.0004
        )

        # 3. 创建策略实例
        strategy = SignalGridStrategy(strategy_config, backtest_client)

        # 5. 创建回测任务
        # 准备历史数据字典
        historical_data = {timeframe: historical_klines}
        backtest_task = BacktestTask(symbol, strategy, backtest_client, historical_data)

        # 6. 创建回测事件循环
        def progress_callback(current, total):
            if current % 100 == 0:  # 每100根K线打印一次进度
                progress = (current / total) * 100
                logger.info(f"回测进度: {progress:.1f}% ({current}/{total})")

        event_loop = BacktestEventLoop(
            historical_klines=historical_klines,
            on_progress_callback=progress_callback,
        )
        event_loop.set_backtest_client(backtest_client)

        # 7. 添加任务并运行回测
        event_loop.add_task(backtest_task)

        logger.info("开始回测...")
        event_loop.start()  # 同步阻塞直到完成
        event_loop.stop()

        # 8. 获取回测结果
        results = backtest_task.get_results()
        trade_history = results['trade_history']

        logger.info(f"回测完成! 总交易次数: {len(trade_history)}")
        logger.info(f"最终余额: ${results['final_balance']:.2f}")

        # 9. 分析结果
        analyzer = BacktestAnalyzer(initial_balance)
        analysis = analyzer.analyze(trade_history)

        # 10. 生成报告
        report_file = f"backtest_report_{symbol.simple()}_{timeframe}.txt"
        report = analyzer.generate_report(analysis, report_file)

        # 打印关键指标
        summary = analysis['summary']
        risk = analysis['risk_metrics']
        trade = analysis['trade_metrics']

        print("\n" + "="*50)
        print("BACKTEST RESULTS")
        print("="*50)
        print(f"Symbol: {symbol.simple()}")
        print(f"Timeframe: {timeframe}")
        print(f"Total Trades: {summary['total_trades']}")
        print(f"Total Return: ${summary['total_return']:.2f} ({summary['total_return_pct']:.2f}%)")
        print(f"Annualized Return: {summary['annualized_return_pct']:.2f}%")
        print(f"Win Rate: {trade['win_rate_pct']:.2f}%")
        print(f"Max Drawdown: {risk['max_drawdown_pct']:.2f}%")
        print(f"Sharpe Ratio: {risk['sharpe_ratio']:.2f}")
        print(f"Report saved to: {report_file}")

    except Exception as e:
        logger.error(f"回测过程中发生错误: {e}")
        import traceback
        traceback.print_exc()


def create_sample_data():
    """
    创建示例数据文件（演示用）
    """
    import pandas as pd
    import numpy as np

    # 生成示例BTC/USDT数据
    np.random.seed(42)  # 固定随机种子

    # 从2023年开始，每小时一条数据
    start_date = pd.Timestamp('2023-01-01')
    end_date = pd.Timestamp('2023-12-31')
    timestamps = pd.date_range(start_date, end_date, freq='H')

    # 生成模拟价格数据
    n_points = len(timestamps)
    base_price = 30000  # 基准价格

    # 使用随机游走生成价格
    price_changes = np.random.normal(0, 0.005, n_points)  # 平均0，标准差0.5%的变化
    prices = base_price * np.exp(np.cumsum(price_changes))

    # 生成OHLCV数据
    data = []
    for i, (ts, close) in enumerate(zip(timestamps, prices)):
        # 简单的OHLC生成
        volatility = 0.002
        high = close * (1 + abs(np.random.normal(0, volatility)))
        low = close * (1 - abs(np.random.normal(0, volatility)))
        open_price = data[-1]['close'] if data else close
        volume = np.random.uniform(100, 1000)  # 随机交易量

        data.append({
            'timestamp': int(ts.timestamp() * 1000),
            'open': open_price,
            'high': high,
            'low': low,
            'close': close,
            'volume': volume
        })

    # 保存到CSV
    df = pd.DataFrame(data)
    os.makedirs('data', exist_ok=True)
    df.to_csv('data/btcusdt_1h_sample.csv', index=False)
    logger.info("示例数据已保存到 data/btcusdt_1h_sample.csv")
    logger.info(f"生成了 {len(data)} 条数据记录")


def command_line_runner():
    import argparse

    parser = argparse.ArgumentParser(description='运行回测')
    parser.add_argument('--create-sample', action='store_true',
                       help='创建示例数据文件')
    parser.add_argument('--data-file', type=str, default='data/btcusdt_1h.csv',
                       help='历史数据文件路径')

    args = parser.parse_args()

    if args.create_sample:
        create_sample_data()
    else:
        # 使用指定的数据文件
        run_backtest_example(args.data_file)

if __name__ == "__main__":
    symbol = Symbol(base="eth", quote="usdt")
    timeframe = "15m"

    run_backtest_example(
        symbol=symbol,
        timeframe=timeframe,
        start_time="2026-01-01",
        end_time="2026-03-19",
        strategy_config=SignalGridStrategyConfig(
            symbol=symbol,
            timeframe=timeframe,
            position_side=PositionSide.LONG,
            master_side=OrderSide.BUY,
            per_order_qty=0.02,
            grid_spacing_rate=0.1,
            max_order=24,
            enable_exit_signal=True,
            signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
            exit_signal_take_profit_min_rate=0.15,
            fixed_rate_take_profit=True,
            fixed_take_profit_rate=0.15,
            order_file_path=f'{DATA_PATH}/signal_grid_long_buy_{symbol.simple()}_{timeframe}.json',
            enable_order_stop_loss=True,
            order_stop_loss_rate=0.02,
            enable_trailing_stop=True,
            trailing_stop_rate=0.02,
            trailing_stop_activation_profit_rate=0.02,
        ),
    )
