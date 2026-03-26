import logging
from datetime import datetime, timezone
from typing import Any, Callable

from backtest.analyzer import BacktestAnalyzer
from backtest.backtest_client import BacktestClient
from backtest.backtest_event_loop import BacktestEventLoop
from backtest.config import BacktestConfig
from backtest.data_loader import HistoricalDataLoader
from backtest.result import BacktestResult
from event_loop.handler.kline_handler import KlineHandler
from model import Kline
from strategy.registry import StrategyRegistry

logger = logging.getLogger(__name__)


class BacktestRunner:
    """High-level backtest orchestrator that replaces procedural scripts."""

    def __init__(self, config: BacktestConfig,
                 strategy_factory: Callable[[BacktestClient], Any] | None = None) -> None:
        self.config = config
        self._strategy_factory = strategy_factory

    def run(self) -> BacktestResult:
        data_loader = HistoricalDataLoader()
        client = BacktestClient(
            initial_balance=self.config.initial_balance,
            maker_fee=self.config.maker_fee,
            taker_fee=self.config.taker_fee,
        )

        klines = self._load_data(data_loader, client)
        if not klines:
            logger.warning("No historical data loaded, returning empty result")
            return self._empty_result()

        klines.sort(key=lambda k: k.timestamp)

        strategy = self._create_strategy(client)
        handler = KlineHandler(strategy)

        start_ts = self._parse_timestamp(self.config.start_date)
        event_loop = BacktestEventLoop(
            historical_klines=klines,
            start_timestamp=start_ts,
        )
        event_loop.set_backtest_client(client)
        event_loop.add_handler(handler)

        logger.info("Starting backtest...")
        event_loop.start()
        event_loop.stop()

        trade_history = client.get_trade_history()
        final_balance = client.get_final_balance()

        analyzer = BacktestAnalyzer(self.config.initial_balance)
        analysis = analyzer.analyze(trade_history)
        report = analyzer.generate_report(analysis)

        logger.info("Backtest completed. Trades: %d, Final balance: %.2f",
                     len(trade_history), final_balance)

        return BacktestResult(
            analysis=analysis,
            trade_history=trade_history,
            final_balance=final_balance,
            report=report,
        )

    def _load_data(self, data_loader: HistoricalDataLoader,
                   client: BacktestClient) -> list[Kline]:
        symbol = self.config.symbol
        timeframe = self.config.timeframe
        file_path = data_loader.ensure_data(
            symbol, timeframe,
            self.config.start_date, self.config.end_date,
            "data",
        )
        klines = data_loader.load_csv(file_path, symbol, timeframe)
        if klines:
            client.load_historical_data(symbol, timeframe, klines)
            logger.info("Loaded %d klines for %s %s", len(klines), symbol.binance(), timeframe)
        return klines

    def _create_strategy(self, client: BacktestClient) -> Any:
        if self._strategy_factory is not None:
            return self._strategy_factory(client)

        strategy_cls = StrategyRegistry.get(self.config.strategy_type)
        return strategy_cls(client, self.config.strategy_config)

    @staticmethod
    def _parse_timestamp(date_str: str) -> int:
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)

    def _empty_result(self) -> BacktestResult:
        analyzer = BacktestAnalyzer(self.config.initial_balance)
        analysis = analyzer.analyze([])
        return BacktestResult(
            analysis=analysis,
            trade_history=[],
            final_balance=self.config.initial_balance,
            report=analyzer.generate_report(analysis),
        )
