#!/usr/bin/env python3
"""
AlphaTrendStrategy 回测运行脚本
测试15m主K线和5分钟辅助K线的AlphaTrendStrategy
"""

import os
from backtest.data_loader import HistoricalDataLoader
from backtest.backtest_client import BacktestClient
from backtest.multi_timeframe_backtest_event_loop import MultiTimeframeBacktestEventLoop
from backtest.analyzer import BacktestAnalyzer
from task.backtest_task import BacktestTask
from template.ethusdt import alpha_trend
import log
import time

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
    timeframes = list(data_files.keys())

    # 检查数据文件是否存在
    for timeframe, file_path in data_files.items():
        if not os.path.exists(file_path):
            logger.error(f"数据文件不存在: {file_path}")
            logger.info("请先下载相应时间框架的历史数据")
            return

    # Delete strategy state file for fresh backtest
    state_file = "data/alpha_trend_ethusdt.json"
    if os.path.exists(state_file):
        os.remove(state_file)
        logger.info("Removed existing strategy state file for fresh backtest")

    try:
        
        initial_balance = 10000.0
        backtest_client = BacktestClient(
            initial_balance=initial_balance,
            maker_fee=0.0005,  # 0.05%
            taker_fee=0.0005   # 0.05%
        )
        strategy_task = alpha_trend(backtest_client)
        strategy = strategy_task.strategy
        symbol = strategy_task.symbol

        logger.info("加载历史数据...")
        data_loader = HistoricalDataLoader()
        historical_data = {}

        for timeframe, file_path in data_files.items():
            klines = data_loader.load_csv(file_path, symbol, timeframe)
            historical_data[timeframe] = klines
            logger.info(f"加载了 {len(klines)} 根{timeframe} K线数据")


        backtest_task = BacktestTask(symbol, strategy, backtest_client, historical_data)

        # 5. 创建多时间框架回测事件循环
        def progress_callback(current, total):
            if current % 100 == 0:  # 每100根K线打印一次进度
                progress = (current / total) * 100
                logger.info(f"回测进度: {progress:.1f}% ({current}/{total})")

        event_loop = MultiTimeframeBacktestEventLoop(
            historical_data=historical_data,
            on_progress_callback=progress_callback,
            start_index=start_index
        )
        event_loop.set_backtest_client(backtest_client)

        event_loop.add_task(backtest_task)

        logger.info("开始AlphaTrendStrategy回测...")
        event_loop.start()

        while not event_loop.is_completed:
            time.sleep(0.1)

        event_loop.stop()

        time.sleep(1)

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

        # print("\n" + "="*60)
        # print("ALPHA TREND STRATEGY BACKTEST RESULTS")
        # print("="*60)
        # print(f"Symbol: {symbol.simple()}")
        # print(f"Timeframes: {timeframes[0]} (main), {timeframes[1]} (auxiliary)")
        # print(f"Total Trades: {summary['total_trades']}")
        # print(f"Total Return: ${summary['total_return']:.2f} ({summary['total_return_pct']:.2f}%)")
        # print(f"Annualized Return: {summary['annualized_return_pct']:.2f}%")
        # print(f"Win Rate: {trade['win_rate_pct']:.2f}%")
        # print(f"Max Drawdown: {risk['max_drawdown_pct']:.2f}%")
        # print(f"Sharpe Ratio: {risk['sharpe_ratio']:.2f}")
        # print(f"Report saved to: {report_file}")

    except Exception as e:
        logger.error(f"回测过程中发生错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_alpha_trend_backtest()
