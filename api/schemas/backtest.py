from typing import Any
from pydantic import BaseModel


class BacktestRequest(BaseModel):
    strategy_type: str
    symbol: str
    timeframe: str
    start_date: str
    end_date: str
    initial_balance: float = 10000.0
    strategy_config: dict[str, Any] = {}


class TradeRecordResponse(BaseModel):
    order_id: str
    side: str
    symbol: str
    position_side: str
    quantity: float
    price: float
    timestamp: int
    fee: float
    pnl: float | None = None


class BacktestSummaryResponse(BaseModel):
    total_trades: int
    total_return: float
    total_return_pct: float
    annualized_return: float
    annualized_return_pct: float
    total_fees: float
    net_return: float


class BacktestRiskMetricsResponse(BaseModel):
    volatility: float
    max_drawdown: float
    max_drawdown_pct: float
    sharpe_ratio: float


class BacktestTradeMetricsResponse(BaseModel):
    win_rate: float
    win_rate_pct: float
    profit_factor: float
    avg_trade_return: float
    best_trade: float
    worst_trade: float


class EquityPointResponse(BaseModel):
    timestamp: int
    equity: float
    return_: float


class CompletedTradeResponse(BaseModel):
    symbol: str
    position_side: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    total_fees: float
    net_pnl: float
    entry_time: str
    exit_time: str


class BacktestResultResponse(BaseModel):
    summary: BacktestSummaryResponse
    risk_metrics: BacktestRiskMetricsResponse
    trade_metrics: BacktestTradeMetricsResponse
    equity_curve: list[EquityPointResponse]
    completed_trades: list[CompletedTradeResponse]
