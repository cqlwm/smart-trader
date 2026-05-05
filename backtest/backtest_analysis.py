from typing import Any

from backtest.analyzer import BacktestAnalyzer
from backtest.backtest_client import BacktestClient


class BacktestAnalysis:
    """从 BacktestClient 状态数据生成回测分析"""

    def __init__(self, client: BacktestClient, initial_balance: float) -> None:
        self.client = client
        self.initial_balance = initial_balance
        self._analyzer = BacktestAnalyzer(initial_balance)

    def analyze(self) -> dict[str, Any]:
        trade_history = self.client.get_trade_history()
        positions = self.client.positions()
        final_balance = self.client.get_final_balance()

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
