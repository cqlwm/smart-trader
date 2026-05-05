import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pandas as pd

from backtest.trade_analysis import TradeAnalysis as BacktestAnalysis
from backtest.backtest_client import BacktestClient
from backtest.backtest_event_loop import BacktestEventLoop
from backtest.config import BacktestConfig
from backtest.kline_data_store import KlineDataStore
from backtest.result import BacktestResult
from event_loop.handler.kline_handler import KlineHandler
from model import Kline, Symbol
from persistence.order_repository import InMemoryOrderRepository
from strategy import GeneralStrategy
from strategy.registry import StrategyRegistry

logger = logging.getLogger(__name__)


class BacktestRunner:
    """High-level backtest orchestrator that replaces procedural scripts."""

    def __init__(self, config: BacktestConfig,
                 strategy_factory: Callable[[BacktestClient], Any] | None = None) -> None:
        self.config = config
        self._strategy_factory = strategy_factory

    def run(self) -> BacktestResult:
        data_loader = KlineDataStore()
        client = BacktestClient(
            order_repo=InMemoryOrderRepository(),
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
        self._preinitialize_klines(strategy, client, klines)
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

        backtest_analysis = BacktestAnalysis(client, self.config.initial_balance)
        analysis = backtest_analysis.analyze()
        report = backtest_analysis.report()

        final_balance = client.get_final_balance()
        trade_history = client.get_trade_history()

        logger.info("Backtest completed. Trades: %d, Final balance: %.2f",
                     len(trade_history), final_balance)

        return BacktestResult(
            analysis=analysis,
            trade_history=trade_history,
            final_balance=final_balance,
            report=report,
        )

    def _load_data(self, data_loader: KlineDataStore,
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

        for tf in self.config.extra_timeframes:
            # Higher timeframes need a wider date range to accumulate enough bars
            # for SMC analysis (which requires >= 50 closed bars)
            tf_offset = self._extra_tf_offset(tf)
            tf_path = data_loader.ensure_data(
                symbol, tf,
                self.config.start_date, self.config.end_date,
                "data",
                offset=tf_offset,
            )
            tf_klines = data_loader.load_csv(tf_path, symbol, tf)
            if tf_klines:
                client.load_historical_data(symbol, tf, tf_klines)
                logger.info("Loaded %d klines for %s %s", len(tf_klines), symbol.binance(), tf)

        return klines

    def _preinitialize_klines(self, strategy: Any, client: BacktestClient, klines: list[Kline]) -> None:
        """Pre-populate kline_data_dict for all timeframes using BacktestClient data.

        BacktestEventLoop only replays the entry timeframe K-lines, so multi-timeframe
        strategies never receive events for other timeframes. This method pre-fills
        the strategy's kline_data_dict so that klines() calls work for all timeframes.
        """
        if not isinstance(strategy, GeneralStrategy):
            return

        # Set current_timestamp to the first kline so fetch_ohlcv can find data
        if klines:
            client.update_current_timestamp(klines[0].timestamp)

        for symbol in strategy.symbols:
            for tf in strategy.timeframes:
                ohlcv = client.fetch_ohlcv(symbol, tf, limit=strategy.init_kline_nums)
                if not ohlcv:
                    logger.warning("No data to pre-initialize %s %s", symbol.binance(), tf)
                    continue

                df = pd.DataFrame([k.to_dict() for k in ohlcv])
                if symbol not in strategy.kline_data_dict:
                    strategy.kline_data_dict[symbol] = {}
                if tf not in strategy.kline_data_dict[symbol]:
                    strategy.kline_data_dict[symbol][tf] = strategy._create_empty_kline_data(tf)
                strategy.kline_data_dict[symbol][tf].klines = df
                logger.info("Pre-initialized %s %s with %d klines", symbol.binance(), tf, len(df))

    @staticmethod
    def _extra_tf_offset(timeframe: str) -> timedelta | None:
        """Calculate how far back to extend data for higher timeframes."""
        tf_minutes = {"1w": 10080, "1d": 1440, "4h": 240, "1h": 60}
        minutes = tf_minutes.get(timeframe, 0)
        if minutes == 0:
            return None
        return timedelta(minutes=minutes * 100)

    def _create_strategy(self, client: BacktestClient) -> Any:
        if self._strategy_factory is not None:
            return self._strategy_factory(client)

        strategy_cls, _config_cls = StrategyRegistry.get(self.config.strategy_type)
        return strategy_cls(client, self.config.strategy_config)

    @staticmethod
    def _parse_timestamp(date_str: str) -> int:
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)

    def _empty_result(self) -> BacktestResult:
        client = BacktestClient(order_repo=InMemoryOrderRepository(), initial_balance=self.config.initial_balance)
        backtest_analysis = BacktestAnalysis(client, self.config.initial_balance)
        analysis = backtest_analysis.analyze()
        return BacktestResult(
            analysis=analysis,
            trade_history=[],
            final_balance=self.config.initial_balance,
            report=backtest_analysis.report(),
        )
