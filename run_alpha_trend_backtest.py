#!/usr/bin/env python3
"""
AlphaTrendStrategy 回测运行脚本
测试15m主K线和5分钟辅助K线的AlphaTrendStrategy
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
from backtest.multi_timeframe_backtest_event_loop import MultiTimeframeBacktestEventLoop
from backtest.analyzer import BacktestAnalyzer
from task.backtest_task import BacktestTask
from template.ethusdt import alpha_trend
import log

logger = log.getLogger(__name__)


def run_alpha_trend_backtest(data_files=None, start_index=300):
    """
    运行AlphaTrendStrategy回测

    Args:
        data_files: 数据文件字典，key为timeframe，value为文件路径
        start_index: 回测起始索引，默认300（为MultiTimeframeStrategy预留初始化数据）
    """
    if data_files is None:
        data_files = {
            "15m": "data/ethusdt_2025_10_15m.csv",
            "5m": "data/ethusdt_2025_10_5m.csv"
        }

    # 配置参数
    symbol = Symbol(base="ETH", quote="USDT")
    timeframes = ["15m", "5m"]

    # 检查数据文件是否存在
    for timeframe, file_path in data_files.items():
        if not os.path.exists(file_path):
            logger.error(f"数据文件不存在: {file_path}")
            logger.info("请先下载相应时间框架的历史数据")
            return

    try:
        # Delete strategy state file for fresh backtest
        state_file = "data/alpha_trend_ethusdt.json"
        if os.path.exists(state_file):
            os.remove(state_file)
            logger.info("Removed existing strategy state file for fresh backtest")

        # 1. 加载历史数据
        logger.info("加载历史数据...")
        data_loader = HistoricalDataLoader()
        historical_data = {}

        for timeframe, file_path in data_files.items():
            klines = data_loader.load_csv(file_path, symbol, timeframe)
            historical_data[timeframe] = klines
            logger.info(f"加载了 {len(klines)} 根{timeframe} K线数据")

        # 2. 创建回测客户端
        initial_balance = 10000.0
        backtest_client = BacktestClient(
            initial_balance=initial_balance,
            maker_fee=0.0002,  # 0.02%
            taker_fee=0.0004   # 0.04%
        )

        # 3. 创建策略实例（模拟交易所客户端）
        class MockExClient:
            pass

        mock_client = MockExClient()

        # 使用模板创建策略任务
        strategy_task = alpha_trend(mock_client)
        strategy = strategy_task.strategy

        # For backtesting, reset strategy state to start fresh
        strategy.position = None
        strategy.current_monitor_timeframe_index = 0
        strategy.total_trades = 0
        strategy.winning_trades = 0
        strategy.total_pnl = 0.0

        # 4. 创建回测任务
        backtest_task = BacktestTask(symbol, strategy, backtest_client, historical_data)

        # 5. 创建多时间框架回测事件循环
        def progress_callback(current, total):
            if current % 100 == 0:  # 每100根K线打印一次进度
                progress = (current / total) * 100
                logger.info(f"回测进度: {progress:.1f}% ({current}/{total})")

        event_loop = MultiTimeframeBacktestEventLoop(
            historical_data=historical_data,
            speed_multiplier=0.0,  # 手动控制速度，便于观察
            on_progress_callback=progress_callback,
            start_index=start_index
        )
        event_loop.set_backtest_client(backtest_client)

        # 6. 添加任务并运行回测
        event_loop.add_task(backtest_task)

        logger.info("开始AlphaTrendStrategy回测...")
        event_loop.start()

        # 等待回测完成
        while not event_loop.is_completed:
            import time
            time.sleep(0.1)

        event_loop.stop()

        # 等待所有异步操作完成
        import time
        time.sleep(1)

        # 7. 获取回测结果
        results = backtest_task.get_results()
        trade_history = results['trade_history']

        logger.info(f"回测完成! 总交易次数: {len(trade_history)}")
        logger.info(f"最终余额: ${results['final_balance']:.2f}")

        # Debug: Print trade history details
        if trade_history:
            logger.info(f"First trade: {trade_history[0]}")
        else:
            logger.info("No trades in history")

        # Debug: Check backtest client order history
        backtest_orders = backtest_client.get_trade_history()
        logger.info(f"Backtest client has {len(backtest_orders)} orders")
        if backtest_orders:
            logger.info(f"First order: {backtest_orders[0]}")

        # 8. 分析结果
        analyzer = BacktestAnalyzer(initial_balance)
        analysis = analyzer.analyze(trade_history)

        # 9. 生成报告
        report_file = f"backtest_report_alpha_trend_{symbol.simple()}_{timeframes[0]}_{timeframes[1]}.txt"
        report = analyzer.generate_report(analysis, report_file)

        # 打印关键指标
        summary = analysis['summary']
        risk = analysis['risk_metrics']
        trade = analysis['trade_metrics']

        print("\n" + "="*60)
        print("ALPHA TREND STRATEGY BACKTEST RESULTS")
        print("="*60)
        print(f"Symbol: {symbol.simple()}")
        print(f"Timeframes: {timeframes[0]} (main), {timeframes[1]} (auxiliary)")
        print(f"Total Trades: {summary['total_trades']}")
        print(f"Total Return: ${summary['total_return']:.2f} ({summary['total_return_pct']:.2f}%)")
        print(f"Annualized Return: {summary['annualized_return_pct']:.2f}%")
        print(f"Win Rate: {trade['win_rate_pct']:.2f}%")
        print(f"Max Drawdown: {risk['max_drawdown_pct']:.2f}%")
        print(f"Sharpe Ratio: {risk['sharpe_ratio']:.2f}")
        print(f"Report saved to: {report_file}")

        # 打印策略统计信息
        if hasattr(strategy, 'total_trades') and hasattr(strategy, 'winning_trades'):
            print("\nStrategy Stats:")
            print(f"Strategy Total Trades: {strategy.total_trades}")
            print(f"Strategy Winning Trades: {strategy.winning_trades}")
            print(f"Strategy Total P&L: ${strategy.total_pnl:.2f}")

    except Exception as e:
        logger.error(f"回测过程中发生错误: {e}")
        import traceback
        traceback.print_exc()


def command_line_runner():
    import argparse

    parser = argparse.ArgumentParser(description='运行AlphaTrendStrategy回测')
    parser.add_argument('--data-15m', type=str, default='data/ethusdt_2025_10_15m.csv',
                       help='15分钟K线数据文件路径')
    parser.add_argument('--data-5m', type=str, default='data/ethusdt_2025_10_5m.csv',
                       help='5分钟K线数据文件路径')
    parser.add_argument('--start-index', type=int, default=300,
                       help='回测起始索引')

    args = parser.parse_args()

    data_files = {
        "15m": args.data_15m,
        "5m": args.data_5m
    }

    run_alpha_trend_backtest(data_files, args.start_index)


if __name__ == "__main__":
    # command_line_runner()
    run_alpha_trend_backtest()
