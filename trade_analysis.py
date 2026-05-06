from typing import Any

from backtest.analyzer import BacktestAnalyzer
from client.ex_client import ExSwapClient


class TradeAnalysis:
    """从 ExSwapClient 状态数据生成交易分析（通用，不限于回测）"""

    def __init__(self, client: ExSwapClient, initial_balance: float | None = None) -> None:
        self.client = client
        self.initial_balance = initial_balance if initial_balance is not None else client.balance('USDT')
        self._analyzer = BacktestAnalyzer(self.initial_balance)

    def analyze(self) -> dict[str, Any]:
        trade_history = self.client.get_trade_history()
        positions = self.client.positions()
        final_balance = self.client.balance('USDT')

        analysis = self._analyzer.analyze(trade_history)
        analysis['final_state'] = {
            'balance': final_balance,
            'open_positions': positions,
            'pnl': final_balance - self.initial_balance,
        }
        return analysis

    def report(self) -> str:
        analysis = self.analyze()
        return self._analyzer.generate_report(analysis)
