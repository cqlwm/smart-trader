import pytest
from model import Symbol, Kline, OrderSide, PositionSide, OrderStatus
from backtest.backtest_client import BacktestClient
from backtest.backtest_analysis import BacktestAnalysis
from persistence.order_repository import InMemoryOrderRepository


SYMBOL = Symbol(base='eth', quote='usdt')
TS_BASE = 1_700_000_000_000


def _client_with_trade() -> BacktestClient:
    repo = InMemoryOrderRepository()
    client = BacktestClient(order_repo=repo, initial_balance=10_000.0)
    client.update_current_timestamp(TS_BASE)
    client.current_prices[SYMBOL.binance()] = 2000.0

    client.place_order_v2('test', 'entry', SYMBOL, OrderSide.BUY, 1.0,
                          position_side=PositionSide.LONG)
    client.update_current_timestamp(TS_BASE + 3600_000)
    client.current_prices[SYMBOL.binance()] = 2200.0
    client.place_order_v2('test', 'exit_entry', SYMBOL, OrderSide.SELL, 1.0,
                          position_side=PositionSide.LONG)
    return client


class TestBacktestAnalysis:
    def test_analyze_returns_dict(self) -> None:
        client = _client_with_trade()
        analysis = BacktestAnalysis(client, initial_balance=10_000.0)
        result = analysis.analyze()
        assert isinstance(result, dict)
        assert 'summary' in result
        assert 'risk_metrics' in result
        assert 'trade_metrics' in result
        assert 'final_state' in result

    def test_final_state_includes_balance(self) -> None:
        client = _client_with_trade()
        analysis = BacktestAnalysis(client, initial_balance=10_000.0)
        result = analysis.analyze()
        assert 'balance' in result['final_state']
        assert 'pnl' in result['final_state']

    def test_report_returns_string(self) -> None:
        client = _client_with_trade()
        analysis = BacktestAnalysis(client, initial_balance=10_000.0)
        report = analysis.report()
        assert isinstance(report, str)
        assert "BACKTEST REPORT" in report

    def test_empty_backtest(self) -> None:
        client = BacktestClient(order_repo=InMemoryOrderRepository(), initial_balance=10_000.0)
        analysis = BacktestAnalysis(client, initial_balance=10_000.0)
        result = analysis.analyze()
        assert result['summary']['total_trades'] == 0
        assert result['final_state']['balance'] == 10_000.0
