#!/usr/bin/env python3
"""
回测运行脚本示例
演示如何使用回测系统运行策略
"""

import os
import sys
from pathlib import Path

from strategy.none_strategy import NoneStrategy

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
import log

logger = log.getLogger(__name__)


def run_backtest_example(data_file="data/ethusdt_2025_10_1m.csv", start_index=300):
    """
    运行SignalGridStrategy回测示例

    Args:
        data_file: 数据文件路径
        start_index: 回测起始索引，默认300（为MultiTimeframeStrategy预留初始化数据）
    """
    # 配置参数
    symbol = Symbol(base="ETH", quote="USDT")
    timeframe = "1m"

    # 检查数据文件是否存在
    if not os.path.exists(data_file):
        logger.error(f"数据文件不存在: {data_file}")
        logger.info("请准备CSV格式的历史数据文件，包含列: timestamp,open,high,low,close,volume")
        return

    try:
        # 1. 加载历史数据
        logger.info("加载历史数据...")
        data_loader = HistoricalDataLoader()
        historical_klines = data_loader.load_csv(data_file, symbol, timeframe)

        if not historical_klines:
            logger.error("未加载到历史数据")
            return

        logger.info(f"加载了 {len(historical_klines)} 根K线数据")

        # 2. 创建回测客户端
        initial_balance = 10000.0
        backtest_client = BacktestClient(
            initial_balance=initial_balance,
            maker_fee=0.0002,  # 0.02%
            taker_fee=0.0004   # 0.04%
        )

        # 4. 创建策略实例
        strategy = NoneStrategy(Symbol(base='eth', quote='usdc'), '1m', backtest_client)

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
            start_index=start_index
        )
        event_loop.set_backtest_client(backtest_client)

        # 7. 添加任务并运行回测
        event_loop.add_task(backtest_task)

        logger.info("开始回测...")
        event_loop.start()

        # 等待回测完成
        while not event_loop.is_completed:
            import time
            time.sleep(0.1)

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
    # command_line_runner()
    run_backtest_example(data_file="data/ethusdt_2025_10_1m.csv")
