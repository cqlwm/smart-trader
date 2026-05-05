import logging
from typing import Any

from bot_manager import BotManager
from backtest.backtest_client import BacktestClient
from backtest.backtest_event_loop import BacktestEventLoop
from backtest.config import BacktestConfig
from backtest.kline_data_store import KlineDataStore
from backtest.trade_analysis import TradeAnalysis
from persistence.order_repository import InMemoryOrderRepository

logger = logging.getLogger(__name__)


class BacktestRunner:
    def __init__(self, config: BacktestConfig) -> None:
        self._config = config
        self._backtest_client = self._create_backtest_client()

        self._event_loop = BacktestEventLoop(
            start_index=config.start_index,
        )
        self._event_loop.set_backtest_client(self._backtest_client)
        self._event_loop.subscribe(symbols=[config.symbol], timeframes=[config.timeframe])

        self._bot_manager = BotManager(
            ex_client=self._backtest_client,
            el=self._event_loop,
            config_path=config.config_path,
        )

        logger.info(
            "BacktestRunner initialized: %s %s %s~%s",
            config.symbol.binance(), config.timeframe, config.start_date, config.end_date
        )

    def _create_backtest_client(self) -> BacktestClient:
        data_store = KlineDataStore()
        return BacktestClient(
            order_repo=InMemoryOrderRepository(),
            initial_balance=self._config.initial_balance,
            maker_fee=self._config.maker_fee,
            taker_fee=self._config.taker_fee,
            data_store=data_store,
            symbol=self._config.symbol,
            timeframe=self._config.timeframe,
            start_date=self._config.start_date,
            end_date=self._config.end_date,
            extra_timeframes=self._config.extra_timeframes,
            data_dir=self._config.data_dir,
        )

    def run(self) -> dict[str, Any]:
        self._bot_manager.start_bot()
        self._event_loop.stop()

        trade_analysis = TradeAnalysis(
            client=self._backtest_client,
            initial_balance=self._config.initial_balance,
        )
        analysis = trade_analysis.analyze()

        logger.info(
            "Backtest completed: %d trades, final balance: $%.2f",
            analysis['summary']['total_trades'],
            self._backtest_client.get_final_balance(),
        )

        return analysis

    def report(self) -> str:
        self._bot_manager.start_bot()
        self._event_loop.stop()

        trade_analysis = TradeAnalysis(
            self._backtest_client,
            initial_balance=self._config.initial_balance,
        )
        return trade_analysis.report()
