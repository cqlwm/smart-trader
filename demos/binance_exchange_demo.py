"""BinanceExchange 使用示例

运行:
    uv run python demos/binance_exchange_demo.py                    # 只读查询
    uv run python demos/binance_exchange_demo.py --place-order       # 查询 + 下单/撤单/平仓
    uv run python demos/binance_exchange_demo.py --place-order --base ETH

凭证从环境变量读取(与 run.py 保持一致):
    BINANCE_API_KEY_MAIN / BINANCE_API_SECRET_MAIN / BINANCE_IS_TEST_MAIN

注意:
    1. 下单默认关闭, 需显式传 --place-order, 避免误触发真实委托。
    2. position_side 依赖币安的双向持仓(hedge)模式, 单向持仓账户需改用 BOTH。
"""

import argparse
import os
import secrets

import dotenv

from smart_trader import log
from smart_trader.exchange.binance.binance_exchange import (
    BinanceExchange,
    create_binance_exchange,
)
from smart_trader.model import (
    Kline,
    OrderRequest,
    OrderSide,
    OrderType,
    Position,
    PositionSide,
    Symbol,
    TimeInForce,
)

dotenv.load_dotenv()

log.init_logging()

logger = log.getLogger(__name__)


def create_exchange() -> BinanceExchange:
    api_key = os.environ.get("BINANCE_API_KEY_MAIN")
    api_secret = os.environ.get("BINANCE_API_SECRET_MAIN")
    is_test = os.environ.get("BINANCE_IS_TEST_MAIN") == "True"
    if not api_key or not api_secret:
        raise ValueError("BINANCE_API_KEY_MAIN / BINANCE_API_SECRET_MAIN 未设置")

    logger.info("is_test(测试网): %s", is_test)
    return create_binance_exchange(api_key, api_secret, is_test)


def demo_symbol_info(exchange: BinanceExchange, symbol: Symbol) -> None:
    info = exchange.symbol_info(symbol)
    logger.info(
        "交易规则: tick_size=%s, step_size=%s, min_qty=%s, min_price=%s, max_price=%s",
        info.tick_size,
        info.step_size,
        info.min_qty,
        info.min_price,
        info.max_price,
    )
    logger.info(
        "价格精度=%s, 数量精度=%s, 格式化价格=%s, 格式化数量=%s",
        info.price_precision(),
        info.qty_precision(),
        info.format_price(1.23456789),
        info.format_qty(1.23456789),
    )


def demo_balance(exchange: BinanceExchange) -> None:
    usdt_free = exchange.balance("USDT")
    logger.info("USDT 可用余额: %s", usdt_free)


def demo_positions(exchange: BinanceExchange, symbol: Symbol) -> list[Position]:
    positions = exchange.positions(symbol)
    for position in positions:
        logger.info(
            "持仓: %s, 方向=%s, 数量=%s, 入场价=%s",
            position.symbol.ccxt(),
            position.side.value,
            position.quantity,
            position.entry_price,
        )
    if not positions:
        logger.info("当前无持仓")
    return positions


def demo_fetch_ohlcv(exchange: BinanceExchange, symbol: Symbol, timeframe: str) -> None:
    klines: list[Kline] = exchange.fetch_ohlcv(symbol, timeframe, limit=5)
    for kline in klines:
        logger.info(kline.to_dict())


def demo_place_limit_order(
    exchange: BinanceExchange, symbol: Symbol, quantity: float, last_price: float
) -> str:
    """挂一个只做市价(post-only)的限价买单, 返回 custom_id"""
    symbol_info = exchange.symbol_info(symbol)
    # 买单价格必须低于市价, 否则 post-only 会被拒绝
    price = symbol_info.format_price(last_price - symbol_info.tick_size)

    custom_id = f"demo{secrets.token_hex(nbytes=5)}"
    order = exchange.place(
        OrderRequest(
            custom_id=custom_id,
            symbol=symbol,
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            quantity=symbol_info.format_qty(quantity),
            price=price,
            position_side=PositionSide.LONG,
            time_in_force=TimeInForce.GTX,
        )
    )
    logger.info("限价单已提交: %s, price=%s, 返回=%s", custom_id, price, order)
    return order.custom_id


def demo_query_order(exchange: BinanceExchange, symbol: Symbol, custom_id: str) -> None:
    order = exchange.query(custom_id, symbol)
    if order is not None:
        logger.info(
            "查询订单 %s: status=%s, price=%s",
            custom_id,
            order.status.value,
            order.price,
        )
    else:
        logger.info("未查询到订单 %s", custom_id)


def demo_cancel_order(
    exchange: BinanceExchange, symbol: Symbol, custom_id: str
) -> None:
    result = exchange.cancel(custom_id, symbol)
    logger.info("撤单 %s: %s", custom_id, result)


def demo_place_market_order(
    exchange: BinanceExchange, symbol: Symbol, quantity: float
) -> None:
    symbol_info = exchange.symbol_info(symbol)
    order = exchange.place(
        OrderRequest(
            custom_id="demomarket001",
            symbol=symbol,
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            quantity=symbol_info.format_qty(quantity),
            position_side=PositionSide.LONG,
        )
    )
    logger.info("市价单已提交: %s", order)


def demo_close_position(exchange: BinanceExchange, symbol: Symbol) -> None:
    """先撤掉该合约挂单, 再用反向市价单按实际持仓量平仓"""
    positions = [p for p in demo_positions(exchange, symbol) if p.quantity > 0]
    if not positions:
        logger.info("无持仓, 跳过平仓")
        return

    canceled = exchange.cancel_open_orders(symbol)
    logger.info("已撤销挂单: %s 笔", len(canceled))

    for position in positions:
        # 平仓方向与持仓方向相反
        close_side = (
            OrderSide.SELL if position.side == PositionSide.LONG else OrderSide.BUY
        )
        order = exchange.place(
            OrderRequest(
                custom_id=f"democlose{secrets.token_hex(nbytes=4)}",
                symbol=symbol,
                side=close_side,
                type=OrderType.MARKET,
                quantity=position.quantity,
                position_side=position.side,
            )
        )
        logger.info(
            "平仓 %s: 下单方向=%s, 数量=%s, 返回=%s",
            position.side.value,
            close_side.value,
            position.quantity,
            order,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="BinanceExchange demo")
    parser.add_argument("--base", default="BTC", help="交易对基础币, 默认 DOGE")
    parser.add_argument("--quote", default="USDT", help="交易对计价币, 默认 USDT")
    parser.add_argument("--timeframe", default="1m", help="K线周期, 默认 1m")
    parser.add_argument(
        "--quantity", type=float, default=0.001, help="下单数量, 默认 1"
    )
    parser.add_argument(
        "--place-order", action="store_true", help="开启真实下单流程(默认关闭)"
    )
    args = parser.parse_args()

    symbol = Symbol(base=args.base.upper(), quote=args.quote.upper())
    exchange = create_exchange()

    logger.info("=== 交易规则 ===")
    demo_symbol_info(exchange, symbol)

    logger.info("=== 账户余额 ===")
    demo_balance(exchange)

    logger.info("=== 当前持仓 ===")
    demo_positions(exchange, symbol)

    logger.info("=== K线数据 ===")
    demo_fetch_ohlcv(exchange, symbol, args.timeframe)

    if not args.place_order:
        logger.info("未开启 --place-order, 跳过下单相关演示")
        return

    logger.info("=== 下单流程 ===")
    last_price = exchange.latest_price(symbol)
    logger.info("最新价: %s", last_price)

    custom_id = demo_place_limit_order(exchange, symbol, args.quantity, last_price)
    demo_query_order(exchange, symbol, custom_id)
    demo_cancel_order(exchange, symbol, custom_id)

    logger.info("=== 市价单与平仓 ===")
    demo_place_market_order(exchange, symbol, args.quantity)
    demo_close_position(exchange, symbol)


if __name__ == "__main__":
    main()
