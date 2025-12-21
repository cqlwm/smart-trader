import json
import log
import re
from typing import Any, Dict, List, Optional
from data_event_loop import Task
from model import Symbol, Kline
from strategy import MultiTimeframeStrategy
from backtest.backtest_client import BacktestClient

logger = log.getLogger(__name__)


class BacktestTask(Task):
    """
    回测任务，使用模拟客户端运行策略
    """

    def __init__(self, symbol: Symbol, strategy: MultiTimeframeStrategy, backtest_client: BacktestClient,
                 historical_data: Optional[Dict[str, List[Kline]]] = None):
        super().__init__()
        self.name: str = 'BacktestTask'
        self.symbol: Symbol = symbol
        self.timeframes: List[str] = strategy.timeframes
        self.strategy: MultiTimeframeStrategy = strategy
        self.backtest_client: BacktestClient = backtest_client

        # 设置策略的客户端
        self.strategy.ex_client = backtest_client

        # 加载历史数据到客户端（用于多时间框架策略的fetch_ohlcv）
        if historical_data:
            for timeframe in self.timeframes:
                if timeframe in historical_data:
                    backtest_client.load_historical_data(timeframe, historical_data[timeframe])
                    logger.info(f"Loaded {len(historical_data[timeframe])} historical klines for {timeframe} into backtest client")
                else:
                    logger.warning(f"No historical data provided for timeframe {timeframe}")

    def run(self, data: str) -> None:
        """
        处理回测数据
        data是BacktestEventLoop构造的WebSocket格式消息
        """
        data_obj: Dict[str, Any] = json.loads(data)

        kline_key: str = data_obj.get('stream', '')
        is_kline: bool = '@kline_' in kline_key
        kline: Dict[str, Any] | None = data_obj.get('data', {}).get('k', None)

        if is_kline and kline:
            match = re.match(r'(\w+)(usdt|usdc|btc)@kline_(\d+\w)', kline_key)
            if not match:
                raise ValueError(f'Invalid kline key: {kline_key}')

            kline_obj = Kline(
                symbol=Symbol(base=match.group(1), quote=match.group(2)),
                timeframe=match.group(3),
                open=float(kline['o']),
                high=float(kline['h']),
                low=float(kline['l']),
                close=float(kline['c']),
                volume=float(kline['v']),
                timestamp=int(kline['t']),
                finished=kline.get('x', False)
            )

            # 检查是否是当前任务关注的交易对和时间框架
            if self.symbol.binance() == kline_obj.symbol.binance() and kline_obj.timeframe in self.timeframes:
                try:
                    self.strategy.run(kline_obj)
                except Exception as e:
                    logger.error(f"Error running strategy for kline {kline_obj.timestamp}: {e}")
                    # 继续运行，不中断回测

    def get_results(self) -> Dict[str, Any]:
        """
        获取回测结果
        """
        final_balance = self.backtest_client.get_final_balance()
        trade_history = self.backtest_client.get_trade_history()

        return {
            'symbol': self.symbol.simple(),
            'timeframes': self.timeframes,
            'final_balance': final_balance,
            'trade_history': trade_history,
            'total_trades': len(trade_history),
            'strategy_name': self.strategy.__class__.__name__
        }
