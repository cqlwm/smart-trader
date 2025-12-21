#!/usr/bin/env python3
"""
下载ETH/USDT 2025年10月1-30日 1分钟K线数据
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

def main():
    # 配置参数
    symbol = Symbol(base="ETH", quote="USDT")
    interval = "1m"
    start_time = "2025-10-01"
    end_time = "2025-10-30"
    file_path = "data/ethusdt_2025_10_1m.csv"

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

    except Exception as e:
        logger.error(f"下载数据时发生错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
