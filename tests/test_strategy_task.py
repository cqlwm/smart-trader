from smart_trader.model import Kline, Symbol
from smart_trader.strategy import SingleTimeframeStrategy
from smart_trader.task.strategy_task import StrategyTask
from tests.fakes import FakeMarketDataPort, make_kline

SYMBOL = Symbol(base="DOGE", quote="USDT")


class RecordingStrategy(SingleTimeframeStrategy):
    def __init__(self) -> None:
        super().__init__("1m", FakeMarketDataPort())
        self.received_klines: list[Kline] = []

    def run(self, kline: Kline) -> None:
        self.received_klines.append(kline)


class TestStrategyTask:
    def test_forwards_matching_kline_to_strategy(self):
        strategy = RecordingStrategy()
        task = StrategyTask(SYMBOL, strategy)
        kline = make_kline()

        task.run(kline)

        assert strategy.received_klines == [kline]

    def test_ignores_other_symbol(self):
        strategy = RecordingStrategy()
        task = StrategyTask(Symbol(base="BTC", quote="USDT"), strategy)

        task.run(make_kline())  # DOGE/USDT

        assert strategy.received_klines == [], "非本任务交易对的K线必须忽略"

    def test_ignores_other_timeframe(self):
        strategy = RecordingStrategy()
        task = StrategyTask(SYMBOL, strategy)

        task.run(make_kline(timeframe="5m"))

        assert strategy.received_klines == [], "非本任务周期的K线必须忽略"

    def test_task_name_identifies_symbol_and_timeframes(self):
        task = StrategyTask(SYMBOL, RecordingStrategy())

        assert "DOGE/USDT" in task.name
        assert "1m" in task.name
