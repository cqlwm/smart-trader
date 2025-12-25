#!/usr/bin/env python3
"""
使用SignalGridStrategy回测ETH/USDT数据
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


def run_signal_grid_backtest(data_file="data/ethusdt_2025_10_1m.csv"):
    """
    运行SignalGridStrategy回测
    """
    # 配置参数
    symbol = Symbol(base="eth", quote="usdt")
    timeframe = "1m"

    # 检查数据文件是否存在
    if not os.path.exists(data_file):
        logger.error(f"数据文件不存在: {data_file}")
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

        # 3. 创建策略配置 (使用与template/ethusdt.py相同的配置)
        config = SignalGridStrategyConfig(
            symbol=symbol,
            timeframe=timeframe,
            position_side=PositionSide.LONG,
            master_side=OrderSide.BUY,
            per_order_qty=0.02,
            grid_spacing_rate=0.002,
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
        )

        # 4. 创建策略实例
        strategy = SignalGridStrategy(config, backtest_client)

        # 5. 创建回测任务
        # 准备历史数据字典
        historical_data = {timeframe: historical_klines}
        backtest_task = BacktestTask(symbol, strategy, backtest_client, historical_data)

        # 6. 创建回测事件循环
        def progress_callback(current, total):
            if current % 1000 == 0:  # 每1000根K线打印一次进度
                progress = (current / total) * 100
                logger.info(f"回测进度: {progress:.1f}% ({current}/{total})")

        event_loop = BacktestEventLoop(
            historical_klines=historical_klines,
            on_progress_callback=progress_callback
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
        logger.info(f"最终余额: ${backtest_client.get_final_balance():.2f}")

        # 9. 分析结果
        analyzer = BacktestAnalyzer(initial_balance)
        analysis = analyzer.analyze(trade_history)

        # 10. 生成报告
        report_file = f"backtest_report_signal_grid_{symbol.simple()}_{timeframe}.txt"
        report = analyzer.generate_report(analysis, report_file)

        # 打印关键指标
        summary = analysis['summary']
        risk = analysis['risk_metrics']
        trade = analysis['trade_metrics']

        print("\n" + "="*60)
        print("SIGNAL GRID STRATEGY BACKTEST RESULTS")
        print("="*60)
        print(f"Symbol: {symbol.simple()}")
        print(f"Timeframe: {timeframe}")
        print(f"Data Period: 2025-10-01 to 2025-10-30")
        print(f"Strategy: SignalGridStrategy with AlphaTrendGridsSignal")
        print(f"Total Trades: {summary['total_trades']}")
        print(f"Total Return: ${summary['total_return']:.2f} ({summary['total_return_pct']:.2f}%)")
        print(f"Annualized Return: {summary['annualized_return_pct']:.2f}%")
        print(f"Win Rate: {trade['win_rate_pct']:.2f}%")
        print(f"Max Drawdown: {risk['max_drawdown_pct']:.2f}%")
        print(f"Sharpe Ratio: {risk['sharpe_ratio']:.2f}")
        if summary['total_trades'] > 0:
            print(f"Avg Trade Duration: {trade.get('avg_trade_duration_hours', 0):.1f} hours")
        else:
            print("Avg Trade Duration: N/A (no trades)")
        print(f"Profit Factor: {trade['profit_factor']:.2f}")
        print(f"Report saved to: {report_file}")

    except Exception as e:
        logger.error(f"回测过程中发生错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    run_signal_grid_backtest()
