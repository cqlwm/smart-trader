import secrets
import logging
import pandas as pd
from datetime import datetime, timezone
from typing import List

from model import OrderSide, Symbol
from smc.config import SMCConfig
from smc.engine import SMCEngine
from smc.mtf import MultiTimeframeConfig, MultiTimeframeResult
from smc.strategy.simple import SimpleIntradayConfig, SimpleIntradayStrategy
from strategy import GeneralStrategy
from strategy.registry import register_strategy
from client.ex_client import ExSwapClient

logger = logging.getLogger(__name__)


def _build_order_id(side: OrderSide) -> str:
    return f"{side.value}{secrets.token_hex(nbytes=5)}"


@register_strategy("smc_intraday")
class SMCIntradayStrategy(GeneralStrategy):
    """多周期 SMC 日内策略。

    在 5m K线闭合时触发分析：
    1. 读取 1w/1d/5m 三个周期的已闭合K线
    2. 运行 SMCEngine 分析各周期
    3. 使用 SimpleIntradayStrategy 判断方向和入场
    4. 执行下单
    """

    def __init__(
        self,
        symbols: List[Symbol],
        timeframes: List[str],
        ex_client: ExSwapClient,
        config: dict,
    ):
        super().__init__(symbols=symbols, timeframes=timeframes)
        self.ex_client = ex_client
        self._strategy_config = SimpleIntradayConfig(**config)
        self._smc_strategy = SimpleIntradayStrategy(self._strategy_config)
        self.strategy_id: str = f"smc_intraday_{symbols[0].simple() if symbols else 'unknown'}"
        self.order_repo = ex_client.order_repo
        self._last_action: str = "WAIT"

    def exchange_client(self):
        return self.ex_client

    def on_kline_finished(self, timeframe: str, symbol: Symbol):
        if timeframe != self._strategy_config.entry_timeframe:
            return

        mtf_result = self._build_mtf_result(symbol)
        if mtf_result is None:
            logger.warning("SMC analysis failed: insufficient data for %s", symbol.ccxt())
            return

        signal = self._smc_strategy.analyze_result(mtf_result)
        logger.info(
            "SMC signal: action=%s confidence=%d price=%.2f reasons=%s",
            signal.action,
            signal.confidence,
            signal.current_price,
            signal.direction_context.reasons if signal.direction_context else [],
        )

        if signal.action == self._last_action:
            return

        if signal.action in ("LONG", "SHORT"):
            self._execute_trade(symbol, signal)
            self._last_action = signal.action

    def _build_mtf_result(self, symbol: Symbol) -> MultiTimeframeResult | None:
        results = {}
        for tf in self._strategy_config.timeframes:
            klines_df = self.klines(tf, symbol)
            closed = klines_df[klines_df["finished"] == True].copy()
            closed["datetime"] = pd.to_datetime(closed["datetime"], utc=True)
            smc_df = closed[["datetime", "open", "high", "low", "close", "volume"]].reset_index(drop=True)
            if len(smc_df) < 50:
                return None

            config = self._build_smc_config(tf)
            engine = SMCEngine(config)
            results[tf] = engine.analyze(smc_df)

        mtf_config = MultiTimeframeConfig(
            exchange_id=self._strategy_config.exchange_id,
            symbol=symbol.ccxt(),
            timeframes=self._strategy_config.timeframes,
        )
        return MultiTimeframeResult(
            symbol=symbol.ccxt(),
            analyzed_at=datetime.now(timezone.utc).isoformat(),
            results=results,
            errors={},
            config=mtf_config,
        )

    def _build_smc_config(self, timeframe: str) -> SMCConfig:
        smc_symbol = self.symbols[0].ccxt() if self.symbols else ""
        return SMCConfig(
            exchange_id=self._strategy_config.exchange_id,
            symbol=smc_symbol,
            timeframe=timeframe,
            lookback_bars=500,
        )

    def _execute_trade(self, symbol: Symbol, signal):
        tp = signal.trade_plan
        if tp is None:
            return

        side = OrderSide.BUY if signal.action == "LONG" else OrderSide.SELL
        self.ex_client.place_order_v2(
            strategy_id=self.strategy_id,
            custom_id=_build_order_id(side),
            symbol=symbol,
            order_side=side,
            quantity=tp.position_size,
            price=tp.entry_price,
        )
