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

from typing import List, Tuple, Callable, Any
from datetime import datetime, timedelta, timezone
from model import Symbol, OrderSide, PositionSide
from backtest.data_loader import HistoricalDataLoader
from backtest.backtest_client import BacktestClient
from backtest.backtest_event_loop import BacktestEventLoop
from backtest.analyzer import BacktestAnalyzer
from event_loop.handler.kline_handler import KlineHandler
from strategy.signal_grid_strategy import SignalGridStrategy, SignalGridStrategyConfig
from strategy.daily_trend_strategy import DailyTrendStrategy, DailyTrendStrategyConfig
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_grids_signal import AlphaTrendGridsSignal
from config import DATA_PATH
import log

logger = log.getLogger(__name__)


def run_generic_backtest(
        data_requirements: List[Tuple[Symbol, str]],
        strategy_factory: Callable[[BacktestClient], Any],
        start_time: str,
        end_time: str,
        data_dir: str = "data",
        initial_balance: float = 1000.0,
        data_offset: timedelta | None = None,
        start_index: int = 300
):
    try:
        logger.info("加载历史数据...")
        data_loader = HistoricalDataLoader()
        all_klines = []
        backtest_client = BacktestClient(
            initial_balance=initial_balance,
            maker_fee=0.0002,
            taker_fee=0.0004
        )

        start_dt = datetime.fromisoformat(start_time)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        end_dt = datetime.fromisoformat(end_time)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        start_timestamp = int(start_dt.timestamp() * 1000)
        end_timestamp = int(end_dt.timestamp() * 1000)

        for symbol, timeframe in data_requirements:
            file_path = data_loader.ensure_data(symbol, timeframe, start_time, end_time, data_dir, offset=data_offset)
            klines = data_loader.load_csv(file_path, symbol, timeframe)
            if data_offset:
                klines = data_loader.filter_by_date_range(klines, start_timestamp, end_timestamp)
            if not klines:
                logger.error(f"未加载到历史数据: {symbol.binance()} {timeframe}")
                return

            logger.info(f"加载了 {len(klines)} 根K线数据 for {symbol.binance()} {timeframe}")
            backtest_client.load_historical_data(symbol, timeframe, klines)
            all_klines.extend(klines)

        if not all_klines:
            logger.error("没有任何历史数据被加载")
            return

        # Sort all historical klines by timestamp chronologically
        all_klines.sort(key=lambda k: k.timestamp)

        kline_handler = KlineHandler(strategy_factory(backtest_client))

        def progress_callback(current, total):
            if current % 1000 == 0:  # 每1000根K线打印一次进度
                progress = (current / total) * 100
                logger.info(f"回测进度: {progress:.1f}% ({current}/{total})")

        event_loop = BacktestEventLoop(
            historical_klines=all_klines,
            on_progress_callback=progress_callback,
            start_index=start_index
        )
        event_loop.set_backtest_client(backtest_client)

        event_loop.add_handler(kline_handler)

        logger.info("开始回测...")
        event_loop.start()  # 同步阻塞直到完成
        event_loop.stop()

        trade_history = backtest_client.get_trade_history()

        logger.info(f"回测完成! 总交易次数: {len(trade_history)}")
        logger.info(f"最终余额: ${backtest_client.get_final_balance():.2f}")

        analyzer = BacktestAnalyzer(initial_balance)
        analysis = analyzer.analyze(trade_history)

        # Using the first data requirement for the report name
        main_symbol, main_tf = data_requirements[0]
        report_file = f"backtest_report_{main_symbol.simple()}_{main_tf}.txt"
        report = analyzer.generate_report(analysis, report_file)

        summary = analysis['summary']
        risk = analysis['risk_metrics']
        trade = analysis['trade_metrics']

        print("\n" + "=" * 50)
        print("BACKTEST RESULTS")
        print("=" * 50)
        print(f"Main Symbol: {main_symbol.simple()}")
        print(f"Main Timeframe: {main_tf}")
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


def run_signal_grid_strategy():
    symbol = Symbol(base="eth", quote="usdt")
    timeframe = "5m"
    start_time = "2026-03-01"
    end_time = "2026-03-19"

    config = SignalGridStrategyConfig(
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
    )

    run_generic_backtest(
        data_requirements=[(symbol, timeframe)],
        strategy_factory=lambda client: SignalGridStrategy(config, client),
        start_time=start_time,
        end_time=end_time,
    )


def run_daily_trend_strategy():
    trade_symbol = Symbol(base="DOGE", quote="USDT")
    direction_symbols = [
        Symbol(base="DOGE", quote="USDT"),
        Symbol(base="BTC", quote="USDT"),
        Symbol(base="ETH", quote="USDT"),
        Symbol(base="SOL", quote="USDT"),
    ]

    data_requirements = [(trade_symbol, "5m")]
    for sym in direction_symbols:
        data_requirements.append((sym, "1d"))

    config = DailyTrendStrategyConfig(
        trade_symbol=trade_symbol,
        trade_timeframe="5m",
        direction_symbols=direction_symbols,
        per_order_qty=100.0,
        order_file_path=f'{DATA_PATH}/daily_trend_{trade_symbol.simple()}_5m.json',
        signal=AlphaTrendGridsSignal(AlphaTrendSignal(OrderSide.BUY)),
    )

    run_generic_backtest(
        data_requirements=data_requirements,
        strategy_factory=lambda client: DailyTrendStrategy(config, client),
        start_time="2026-03-19",
        end_time="2026-03-20",
        data_offset=timedelta(days=1),
        start_index=0
    )


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

    args = parser.parse_args()

    if args.create_sample:
        create_sample_data()
    else:
        run_daily_trend_strategy()


if __name__ == "__main__":
    run_daily_trend_strategy()
