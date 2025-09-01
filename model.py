from datetime import datetime


class Symbol:
    def __init__(self, base: str, quote: str):
        self.base = base.upper()
        self.quote = quote.upper()
    
    def ccxt(self):
        return f'{self.base}/{self.quote}'

    def binance(self):
        return f'{self.base}{self.quote}'
    
    def binance_ws_sub_kline(self, timeframe: str):
        return f'{self.binance()}@kline_{timeframe}'.lower()

'''
                row = {
                    'datetime': dt_str,
                    'timestamp': timestamp,
                    'open': open_price,
                    'high': high_price,
                    'low': low_price,
                    'close': close_price,
                    'volume': volume
                }
'''
class Kline:
    def __init__(self, open: float, high: float, low: float, close: float, volume: float, timestamp: int, finished: bool):
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.timestamp = timestamp
        self.datetime = datetime.fromtimestamp(timestamp / 1000).strftime('%Y-%m-%d %H:%M:%S')
        self.finished = finished
        
    def to_dict(self):
        return {
            'datetime': self.datetime,
            # 'timestamp': self.timestamp,
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'close': self.close,
            'volume': self.volume,
            'finished': self.finished
        }