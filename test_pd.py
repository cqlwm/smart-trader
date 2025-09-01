import pandas as pd
from pandas import DataFrame
import numpy as np
from datetime import datetime

class Kline:
    def __init__(self, datetime, open, high, low, close, volume):
        self.datetime = datetime
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
    
    def to_dict(self):
        return {
            'datetime': self.datetime,
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'close': self.close,
            'volume': self.volume
        }

class KlineProcessor:
    def __init__(self):
        self.klines = DataFrame(columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
        self.last_kline = None
    
    def update_kline(self, kline):
        self.last_kline = kline
        
        if len(self.klines) == 0:
            # 如果klines为空，直接添加
            self.klines = pd.concat([self.klines, DataFrame([kline.to_dict()])], ignore_index=True)
        elif self.klines['datetime'].iloc[-1] == kline.datetime:
            # 如果最后一个K线的时间戳相同，更新最后一个K线
            kline_dict = kline.to_dict()
            self.klines.iloc[-1] = pd.Series(kline_dict, index=self.klines.columns)
        else:
            # 如果时间戳不同，添加新的K线
            self.klines = pd.concat([self.klines, DataFrame([kline.to_dict()])], ignore_index=True)

def test_kline_processor():
    print("测试K线处理器...")
    
    processor = KlineProcessor()
    
    # 测试1: 添加第一个K线
    kline1 = Kline(datetime(2023, 1, 1, 10, 0, 0), 100.0, 105.0, 99.0, 102.0, 1000)
    processor.update_kline(kline1)
    
    print(f"添加第一个K线后，数据长度: {len(processor.klines)}")
    print(f"第一个K线数据:\n{processor.klines.iloc[-1]}")
    
    # 测试2: 更新相同时间戳的K线
    kline1_updated = Kline(datetime(2023, 1, 1, 10, 0, 0), 101.0, 106.0, 100.0, 103.0, 1200)
    processor.update_kline(kline1_updated)
    
    print(f"\n更新相同时间戳K线后，数据长度: {len(processor.klines)}")
    print(f"更新后的K线数据:\n{processor.klines.iloc[-1]}")
    
    # 测试3: 添加不同时间戳的新K线
    kline2 = Kline(datetime(2023, 1, 1, 10, 1, 0), 103.0, 107.0, 102.0, 105.0, 1500)
    processor.update_kline(kline2)
    
    print(f"\n添加新时间戳K线后，数据长度: {len(processor.klines)}")
    print(f"所有K线数据:\n{processor.klines}")
    
    # 验证数据正确性
    assert len(processor.klines) == 2, f"预期2条K线，实际{len(processor.klines)}条"
    assert processor.klines.iloc[0]['open'] == 101.0, "第一条K线开盘价不正确"
    assert processor.klines.iloc[1]['close'] == 105.0, "第二条K线收盘价不正确"
    
    print("\n✅ 所有测试通过！")

if __name__ == "__main__":
    test_kline_processor()