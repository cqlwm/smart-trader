import builtins
from pandas import DataFrame
from strategy import Signal
from strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from strategy.alpha_trend_signal.alpha_trend_signal_v2 import AlphaTrendSignalV2
from utils import log

logger = log.build_logger(__name__)
_close = 'close'
_buy_signal = 'buy_signal'
_sell_signal = 'sell_signal'
_short_order = 'short'
_long_order = 'long'


def _history_transactions(df: DataFrame):
    current_position = None
    entry_price = None
    transactions = []

    for index, row in df.iterrows():
        if current_position is None:
            if row[_buy_signal]:
                current_position = _long_order
                entry_price = row[_close]
            elif row[_sell_signal]:
                current_position = _short_order
                entry_price = row[_close]
        elif current_position == _long_order:
            if row[_sell_signal]:
                transactions.append({
                    "type": _long_order,
                    "entry_price": entry_price,
                    "exit_price": row[_close],
                    "is_profit": row[_close] > entry_price,
                })
                current_position = _short_order
                entry_price = row[_close]
        elif current_position == _short_order:
            if row[_buy_signal]:
                transactions.append({
                    "type": _short_order,
                    "entry_price": entry_price,
                    "exit_price": row[_close],
                    "is_profit": row[_close] < entry_price,
                })
                current_position = _long_order
                entry_price = row[_close]

    return transactions


def _find_last_profitable_trade_exit_price(transactions, order_type: str):
    for trade in reversed(transactions):
        if trade["is_profit"] and trade["type"] == order_type:
            return trade["exit_price"]
    return None


class AlphaTrendSignalV3(Signal):
    """
    低频率(Low Frequency)
    在空头趋势盈利时多头，在多头趋势盈利时空头
    """

    def __init__(self, ats: AlphaTrendSignal, quick_profit_taking: bool = False):
        super().__init__(ats.side)
        self.quick_profit_taking = quick_profit_taking
        self.ats = ats
        self.ats_v2 = AlphaTrendSignalV2(ats=ats)
        self.previous_entry_price = None
        self.previous_exit_price = None

    def run(self, kline: DataFrame) -> int:
        ats_kline_status = self.ats.run(kline)

        close_price = kline.iloc[-1][_close]
        if self.previous_exit_price is None:
            self.previous_exit_price = _find_last_profitable_trade_exit_price(
                _history_transactions(kline), _long_order if self.side.upper() == 'BUY' else _short_order
            )

        if self.ats.is_entry(kline):
            self.previous_entry_price = close_price

        if self.ats.is_exit(kline):
            self.previous_exit_price = close_price

        for is_entry, compare_price in [
            (self.ats.is_entry(kline), self.previous_exit_price),
            (self.ats.is_exit(kline), self.previous_entry_price),
        ]:
            if is_entry and compare_price:
                status = 1
                compare_fun = builtins.float.__lt__
                if self.side.upper() == 'SELL':
                    status = -1
                    compare_fun = builtins.float.__gt__
                if compare_fun(close_price, compare_price):
                    logger.info(f'AlphaTrendSignalV3: {self.side}:{close_price}')
                    return status

        if self.ats.is_exit(kline):
            return ats_kline_status

        if self.quick_profit_taking:
            if self.ats_v2.is_exit(kline):
                return self.ats_v2.run(kline)

        return 0
