import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import verify_api_key
from api.schemas.common import BaseResponse
from api.schemas.backtest import (
    BacktestRequest,
    BacktestResultResponse,
    BacktestSummaryResponse,
    BacktestRiskMetricsResponse,
    BacktestTradeMetricsResponse,
    EquityPointResponse,
    CompletedTradeResponse,
)
from api.schemas.strategy_schemas import StrategyTypeInfo
from backtest.client import BacktestClient
from backtest.event_loop import BacktestEventLoop
from backtest.types import BacktestConfig
from trade_analysis import TradeAnalysis
from persistence.kline_data_store import KlineDataStore
from backtest.types import BacktestResult
from event_loop.handler.kline_handler import KlineHandler
from persistence.order_repository import InMemoryOrderRepository
from model import Symbol
from strategy.registry import StrategyRegistry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/backtest", dependencies=[Depends(verify_api_key)])


def _parse_symbol(symbol_str: str) -> Symbol:
    parts = symbol_str.replace(":USDT", "").split("/")
    if len(parts) != 2:
        raise ValueError(f"Invalid symbol format: {symbol_str}. Expected 'BASE/QUOTE' or 'BASE/QUOTE:USDT'")
    return Symbol(base=parts[0], quote=parts[1])


def _build_strategy_factory(
    strategy_type: str,
    symbol: Symbol,
    timeframe: str,
    strategy_config: dict[str, Any],
):
    """Build a factory function that constructs a strategy with the correct arguments for its type."""

    # Remove keys that the factory sets explicitly to avoid duplicate keyword arguments
    config = {k: v for k, v in strategy_config.items()
              if k not in ("symbol", "timeframe", "trade_symbol", "trade_timeframe",
                           "exchange_id", "order_file_path")}

    if strategy_type == "smc_intraday":
        entry_tf = timeframe
        all_timeframes = ["1w", "1d", entry_tf]

        def factory(client: BacktestClient) -> Any:
            from strategy.smc_signal.smc_intraday_strategy import SMCIntradayStrategy
            return SMCIntradayStrategy(
                symbols=[symbol],
                timeframes=all_timeframes,
                ex_client=client,
                config=config,
            )
        return factory

    if strategy_type == "signal_grid":
        from strategy.signal_grid_strategy import SignalGridStrategy, SignalGridStrategyConfig

        def factory(client: BacktestClient) -> Any:
            cfg = SignalGridStrategyConfig(
                symbol=symbol,
                timeframe=timeframe,
                **config,
            )
            return SignalGridStrategy(cfg, client)
        return factory

    if strategy_type == "simple_grid":
        from strategy.simple_grid_strategy import SimpleGridStrategy, SimpleGridStrategyConfig

        def factory(client: BacktestClient) -> Any:
            cfg = SimpleGridStrategyConfig(
                symbol=symbol,
                **config,
            )
            return SimpleGridStrategy(client, cfg, timeframe)
        return factory

    if strategy_type == "daily_trend":
        from strategy.daily_trend_strategy import DailyTrendStrategy, DailyTrendStrategyConfig

        def factory(client: BacktestClient) -> Any:
            cfg = DailyTrendStrategyConfig(
                trade_symbol=symbol,
                trade_timeframe=timeframe,
                direction_symbols=[symbol],
                signal=None,
                **config,
            )
            return DailyTrendStrategy(cfg, client)
        return factory

    raise ValueError(f"No backtest factory for strategy type: {strategy_type}")


def _to_result_response(result: Any) -> BacktestResultResponse:
    analysis = result.analysis
    summary = analysis.get("summary", {})
    risk = analysis.get("risk_metrics", {})
    trade_metrics = analysis.get("trade_metrics", {})
    equity_curve = analysis.get("equity_curve", [])

    summary_resp = BacktestSummaryResponse(
        total_trades=summary.get("total_trades", 0),
        total_return=summary.get("total_return", 0.0),
        total_return_pct=summary.get("total_return_pct", 0.0),
        annualized_return=summary.get("annualized_return", 0.0),
        annualized_return_pct=summary.get("annualized_return_pct", 0.0),
        total_fees=summary.get("total_fees", 0.0),
        net_return=summary.get("net_return", 0.0),
    )

    risk_resp = BacktestRiskMetricsResponse(
        volatility=risk.get("volatility", 0.0),
        max_drawdown=risk.get("max_drawdown", 0.0),
        max_drawdown_pct=risk.get("max_drawdown_pct", 0.0),
        sharpe_ratio=risk.get("sharpe_ratio", 0.0),
    )

    trade_resp = BacktestTradeMetricsResponse(
        win_rate=trade_metrics.get("win_rate", 0.0),
        win_rate_pct=trade_metrics.get("win_rate_pct", 0.0),
        profit_factor=trade_metrics.get("profit_factor", 0.0),
        avg_trade_return=trade_metrics.get("avg_trade_return", 0.0),
        best_trade=trade_metrics.get("best_trade", 0.0),
        worst_trade=trade_metrics.get("worst_trade", 0.0),
    )

    equity_resp = [
        EquityPointResponse(
            timestamp=int(p.get("timestamp", 0)),
            equity=p.get("equity", 0.0),
            return_=p.get("return", 0.0),
        )
        for p in equity_curve
    ]

    completed_trades = []
    for trade in analysis.get("completed_trades", []):
        completed_trades.append(CompletedTradeResponse(
            symbol=trade.get("symbol", ""),
            position_side=trade.get("position_side", ""),
            entry_price=trade.get("entry_price", 0.0),
            exit_price=trade.get("exit_price", 0.0),
            quantity=trade.get("quantity", 0.0),
            pnl=trade.get("pnl", 0.0),
            total_fees=trade.get("total_fees", 0.0),
            net_pnl=trade.get("net_pnl", 0.0),
            entry_time=str(trade.get("entry_time", "")),
            exit_time=str(trade.get("exit_time", "")),
        ))

    return BacktestResultResponse(
        summary=summary_resp,
        risk_metrics=risk_resp,
        trade_metrics=trade_resp,
        equity_curve=equity_resp,
        completed_trades=completed_trades,
    )


@router.post("/run", response_model=BaseResponse[BacktestResultResponse])
async def run_backtest(request: BacktestRequest):
    try:
        symbol = _parse_symbol(request.symbol)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        StrategyRegistry.get(request.strategy_type)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unknown strategy type: {request.strategy_type}")

    try:
        strategy_factory = _build_strategy_factory(
            request.strategy_type, symbol, request.timeframe, request.strategy_config,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    extra_timeframes: tuple[str, ...] = ()
    if request.strategy_type == "smc_intraday":
        entry_tf = request.timeframe
        extra_timeframes = tuple(tf for tf in ("1w", "1d") if tf != entry_tf)

    try:
        data_store = KlineDataStore()
        client = BacktestClient(
            order_repo=InMemoryOrderRepository(),
            initial_balance=request.initial_balance,
            data_store=data_store,
            symbol=symbol,
            timeframe=request.timeframe,
            start_date=request.start_date,
            end_date=request.end_date,
            extra_timeframes=extra_timeframes,
        )

        strategy = strategy_factory(client)
        handler = KlineHandler(strategy)
        all_timeframes = [request.timeframe] + list(extra_timeframes)
        config = BacktestConfig(
            symbol=symbol,
            timeframe=request.timeframe,
            start_date=request.start_date,
            end_date=request.end_date,
            initial_balance=request.initial_balance,
            extra_timeframes=extra_timeframes,
        )
        event_loop = BacktestEventLoop(config=config, backtest_client=client)
        event_loop.subscribe(symbols=[symbol], timeframes=all_timeframes)
        event_loop.add_handler(handler)
        event_loop.start()
        event_loop.stop()

        trade_analysis = TradeAnalysis(client, initial_balance=request.initial_balance)
        analysis = trade_analysis.analyze()

        result = BacktestResult(
            analysis=analysis,
            trade_history=client.get_trade_history(),
            final_balance=client.get_final_balance(),
            report=trade_analysis.report(),
        )
    except Exception as e:
        logger.error("Backtest failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Backtest execution failed: {str(e)}")

    return BaseResponse(data=_to_result_response(result))


@router.get("/strategies", response_model=BaseResponse[list[StrategyTypeInfo]])
async def list_backtest_strategies():
    types = StrategyRegistry.list_types()
    return BaseResponse(data=[StrategyTypeInfo(name=t) for t in types])
