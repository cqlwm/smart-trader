import secrets
from dataclasses import dataclass
import time
from enum import Enum
from typing import Any

import log
from client.ex_client import ExSwapClient
from model import OrderSide, PlaceOrderBehavior, PositionSide, Symbol

logger = log.getLogger(__name__)


class RankingType(str, Enum):
    GAINERS = "gainers"
    LOSERS = "losers"


@dataclass
class ShortOrderRequest:
    symbol: Symbol
    quantity: float
    last_price: float
    percentage: float


def _to_symbol_and_metrics(ticker: dict[str, Any]) -> tuple[Symbol, float, float] | None:
    symbol_text = str(ticker.get("symbol") or "")
    if not symbol_text:
        return None

    symbol_text = symbol_text.split(":")[0]
    if "/" not in symbol_text:
        return None

    base, quote = symbol_text.split("/", 1)
    quote_upper = quote.upper()
    if quote_upper not in {"USDT", "USDC"}:
        return None

    info = ticker.get("info", {})
    if not isinstance(info, dict):
        info = {}

    last_price_raw: Any = ticker.get("last", info.get("lastPrice"))
    percentage_raw: Any = ticker.get("percentage", info.get("priceChangePercent"))
    if last_price_raw is None or percentage_raw is None:
        return None

    try:
        last_price = float(last_price_raw)
        percentage = float(percentage_raw)
    except (TypeError, ValueError):
        return None

    if last_price <= 0:
        return None

    symbol = Symbol(base=base.lower(), quote=quote.lower())
    return symbol, percentage, last_price


def _ticker_timestamp_ms(ticker: dict[str, Any]) -> int | None:
    timestamp_raw: Any = ticker.get("timestamp")
    if timestamp_raw is None:
        info = ticker.get("info", {})
        if isinstance(info, dict):
            timestamp_raw = info.get("closeTime")

    try:
        timestamp = int(timestamp_raw)
    except (TypeError, ValueError):
        return None

    return timestamp if timestamp > 0 else None


def _normalize_ranking_type(ranking_type: str) -> RankingType:
    ranking_type_lower = ranking_type.lower().strip()
    try:
        return RankingType(ranking_type_lower)
    except ValueError as exc:
        raise ValueError("ranking_type must be either 'gainers' or 'losers'") from exc


def fetch_top_gainers(
    exchange_client: ExSwapClient,
    top_n: int = 10,
    ranking_type: str = RankingType.GAINERS.value,
) -> list[tuple[Symbol, float, float]]:
    tickers: dict[str, dict[str, Any]] = exchange_client.exchange.fetch_tickers()  # type: ignore[assignment]
    symbol_rank_map: dict[str, tuple[Symbol, float, float]] = {}
    now_ms = int(time.time() * 1000)
    freshness_ms = 5 * 60 * 1000
    rank_type = _normalize_ranking_type(ranking_type)

    for ticker in tickers.values():
        ticker_ts = _ticker_timestamp_ms(ticker)
        if ticker_ts is None or now_ms - ticker_ts > freshness_ms:
            continue

        parsed = _to_symbol_and_metrics(ticker)
        if parsed is None:
            continue

        symbol, percentage, last_price = parsed
        key = symbol.binance()
        previous = symbol_rank_map.get(key)
        if previous is None:
            symbol_rank_map[key] = (symbol, percentage, last_price)
            continue

        is_better = percentage > previous[1] if rank_type == RankingType.GAINERS else percentage < previous[1]
        if is_better:
            symbol_rank_map[key] = (symbol, percentage, last_price)

    reverse = rank_type == RankingType.GAINERS
    return sorted(symbol_rank_map.values(), key=lambda item: item[1], reverse=reverse)[:top_n]


def build_top10_short_order_requests(
    exchange_client: ExSwapClient,
    per_symbol_usdt: float = 100.0,
    top_n: int = 10,
    ranking_type: str = RankingType.GAINERS.value,
) -> list[ShortOrderRequest]:
    requests: list[ShortOrderRequest] = []
    exchange_client.exchange.set_sandbox_mode(False)
    gainers = fetch_top_gainers(exchange_client, top_n=top_n, ranking_type=ranking_type)
    exchange_client.exchange.set_sandbox_mode(True)

    for symbol, percentage, last_price in gainers:
        raw_qty = per_symbol_usdt / last_price
        quantity = exchange_client.symbol_info(symbol).format_qty(raw_qty)
        if quantity <= 0:
            logger.warning("skip symbol=%s due to zero qty after formatting", symbol.binance())
            continue

        requests.append(
            ShortOrderRequest(
                symbol=symbol,
                quantity=quantity,
                last_price=last_price,
                percentage=percentage,
            )
        )

    return requests


def place_top10_short_orders(
    exchange_client: ExSwapClient,
    per_symbol_usdt: float = 100.0,
    top_n: int = 10,
    ranking_type: str = RankingType.GAINERS.value,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    requests = build_top10_short_order_requests(
        exchange_client=exchange_client,
        per_symbol_usdt=per_symbol_usdt,
        top_n=top_n,
        ranking_type=ranking_type,
    )
    success_orders: list[dict[str, Any]] = []
    failed_orders: list[dict[str, Any]] = []

    rank_type = _normalize_ranking_type(ranking_type)
    rank_label = "top gainers" if rank_type == RankingType.GAINERS else "top losers"
    logger.info(
        "%s selected=%s",
        rank_label,
        [
            f"{request.symbol.binance()}({request.percentage:.2f}%)"
            for request in requests
        ],
    )

    for request in requests:
        custom_id = f"short{secrets.token_hex(nbytes=5)}"
        try:
            order = exchange_client.place_order_v2(
                custom_id=custom_id,
                symbol=request.symbol,
                order_side=OrderSide.SELL,
                quantity=request.quantity,
                position_side=PositionSide.SHORT,
                place_order_behavior=PlaceOrderBehavior.NORMAL,
            )
        except Exception as exc:
            failed_orders.append(
                {
                    "symbol": request.symbol.binance(),
                    "quantity": request.quantity,
                    "error": str(exc),
                }
            )
            logger.error("place short order failed symbol=%s error=%s", request.symbol.binance(), exc)
            continue

        if not order:
            failed_orders.append(
                {
                    "symbol": request.symbol.binance(),
                    "quantity": request.quantity,
                    "error": "empty order response",
                }
            )
            logger.error("place short order failed symbol=%s error=empty order response", request.symbol.binance())
            continue

        success_orders.append(
            {
                "symbol": request.symbol.binance(),
                "quantity": request.quantity,
                "order_id": order.get("clientOrderId", custom_id),
                "status": order.get("status"),
            }
        )
        logger.info(
            "place short order success symbol=%s quantity=%s order_id=%s status=%s",
            request.symbol.binance(),
            request.quantity,
            order.get("clientOrderId", custom_id),
            order.get("status"),
        )

    return success_orders, failed_orders
