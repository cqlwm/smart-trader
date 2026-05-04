#!/usr/bin/env python3
"""
下载ETH/USDT 2025年10月1-30日 K线数据
支持不同时间框架：1m, 5m, 15m
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from model import Symbol
from backtest.data_loader import HistoricalDataLoader
import log

logger = log.getLogger(__name__)

def download_data(interval, file_path):
    """下载指定时间框架的数据"""
    # 配置参数
    symbol = Symbol(base="ETH", quote="USDT")
    start_time = "2025-10-01"
    end_time = "2025-10-30"

    logger.info(f"开始下载 {symbol.simple()} {interval} 数据: {start_time} 到 {end_time}")

    try:
        # 创建数据加载器
        data_loader = HistoricalDataLoader()

        # 下载数据
        saved_file = data_loader.download_and_save_historical_data(
            symbol=symbol,
            interval=interval,
            start_time=start_time,
            end_time=end_time,
            file_path=file_path
        )

        logger.info(f"数据下载完成，保存到: {saved_file}")
        return saved_file

    except Exception as e:
        logger.error(f"下载数据时发生错误: {e}")
        import traceback
        traceback.print_exc()
        return None

def main():
    # 下载不同时间框架的数据
    intervals = ["15m", "5m", "1m"]

    for interval in intervals:
        file_path = f"data/ethusdt_2025_10_{interval}.csv"
        download_data(interval, file_path)

if __name__ == "__main__":
    main()
