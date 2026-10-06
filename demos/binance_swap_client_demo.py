"""BinanceSwapClient 使用示例

运行:
    uv run python demos/binance_swap_client_demo.py                    # 只读查询
    uv run python demos/binance_swap_client_demo.py --place-order       # 查询 + 下单/撤单/平仓
    uv run python demos/binance_swap_client_demo.py --place-order --base ETH

凭证从环境变量读取(与 run.py 保持一致):
    BINANCE_API_KEY_MAIN / BINANCE_API_SECRET_MAIN / BINANCE_IS_TEST_MAIN

注意:
    1. 下单默认关闭, 需显式传 --place-order, 避免误触发真实委托。
    2. position_side 依赖币安的双向持仓(hedge)模式, 单向持仓账户需去掉该参数。
    3. client.close_position() 目前只撤销该合约的挂单, 未实际平仓。
"""

import argparse
import os
import secrets
from typing import Any

import dotenv

from smart_trader import log
from smart_trader.client.binance_client import BinanceSwapClient
from smart_trader.model import (
    Kline,
    OrderSide,
    PlaceOrderBehavior,
    PositionSide,
    Symbol,
)

dotenv.load_dotenv()

logger = log.getLogger(__name__)


def create_client() -> BinanceSwapClient:
    api_key = os.environ.get("BINANCE_API_KEY_MAIN")
    api_secret = os.environ.get("BINANCE_API_SECRET_MAIN")
    is_test = os.environ.get("BINANCE_IS_TEST_MAIN") == "True"
    if not api_key or not api_secret:
        raise ValueError("BINANCE_API_KEY_MAIN / BINANCE_API_SECRET_MAIN 未设置")

    logger.info(f"is_test(测试网): {is_test}")
    return BinanceSwapClient(api_key=api_key, api_secret=api_secret, is_test=is_test)


def demo_symbol_info(client: BinanceSwapClient, symbol: Symbol) -> None:
    info = client.symbol_info(symbol)
    logger.info(
        f"交易规则: tick_size={info.tick_size}, step_size={info.step_size}, "
        f"min_qty={info.min_qty}, min_price={info.min_price}, max_price={info.max_price}"
    )
    logger.info(
        f"价格精度={info.price_precision()}, 数量精度={info.qty_precision()}, "
        f"格式化价格={info.format_price(1.23456789)}, 格式化数量={info.format_qty(1.23456789)}"
    )


def demo_balance(client: BinanceSwapClient) -> None:
    usdt_free = client.balance("USDT")
    logger.info(f"USDT 可用余额: {usdt_free}")


def demo_positions(client: BinanceSwapClient, symbol: Symbol) -> list[dict[str, Any]]:
    # 注意: positions() 需要传 ccxt 统一格式的交易对, 例如 DOGE/USDT
    positions = client.positions(symbol.ccxt())
    for position in positions:
        logger.info(
            f"持仓: {position['symbol']}, 方向={position['side']}, "
            f"数量={position['contracts']}, 入场价={position['entryPrice']}"
        )
    if not positions:
        logger.info("当前无持仓")
    return positions


def demo_fetch_ohlcv(client: BinanceSwapClient, symbol: Symbol, timeframe: str) -> None:
    klines: list[Kline] = client.fetch_ohlcv(symbol, timeframe, limit=5)
    for kline in klines:
        logger.info(kline.to_dict())


def demo_place_limit_order(
    client: BinanceSwapClient, symbol: Symbol, quantity: float, last_price: float
) -> str:
    """挂一个只做市价(post-only)的限价买单, 返回 custom_id"""
    symbol_info = client.symbol_info(symbol)
    # 买单价格必须低于市价, 否则 post-only 会被拒绝
    price = symbol_info.format_price(last_price - symbol_info.tick_size)

    custom_id = f"demo{secrets.token_hex(nbytes=5)}"
    order = client.place_order_v2(
        custom_id=custom_id,
        symbol=symbol,
        order_side=OrderSide.BUY,
        quantity=symbol_info.format_qty(quantity),
        price=price,
        position_side=PositionSide.LONG,
        place_order_behavior=PlaceOrderBehavior.NORMAL,
        time_in_force="GTX",
    )
    logger.info(f"限价单已提交: {custom_id}, price={price}, 返回={order}")
    return custom_id


def demo_query_order(client: BinanceSwapClient, symbol: Symbol, custom_id: str) -> None:
    order = client.query_order(custom_id, symbol)
    if order:
        logger.info(
            f"查询订单 {custom_id}: status={order['status']}, price={order['price']}"
        )
    else:
        logger.info(f"未查询到订单 {custom_id}")


def demo_cancel_order(
    client: BinanceSwapClient, symbol: Symbol, custom_id: str
) -> None:
    result = client.cancel(custom_id, symbol)
    logger.info(f"撤单 {custom_id}: {result}")


def demo_place_market_order(
    client: BinanceSwapClient, symbol: Symbol, quantity: float
) -> None:
    symbol_info = client.symbol_info(symbol)
    order = client.place_order_v2(
        custom_id="demomarket001",
        symbol=symbol,
        order_side=OrderSide.BUY,
        quantity=symbol_info.format_qty(quantity),
        price=None,
        position_side=PositionSide.LONG,
        place_order_behavior=PlaceOrderBehavior.NORMAL,
    )
    logger.info(f"市价单已提交: {order}")


def demo_close_position(client: BinanceSwapClient, symbol: Symbol) -> None:
    """先撤掉该合约挂单, 再用反向市价单按实际持仓量平仓"""
    positions = [
        p for p in demo_positions(client, symbol) if float(p["contracts"] or 0) > 0
    ]
    if not positions:
        logger.info("无持仓, 跳过平仓")
        return

    for position in positions:
        contracts = float(position["contracts"])
        position_side = PositionSide(position["side"].lower())
        # 平仓方向与持仓方向相反
        close_side = (
            OrderSide.SELL if position_side == PositionSide.LONG else OrderSide.BUY
        )

        # close_position 只负责撤掉该合约的挂单(auto_cancel=True), 真正的平仓由下面的反向单完成
        client.close_position(symbol.ccxt(), position_side.value)

        order = client.place_order_v2(
            custom_id=f"democlose{secrets.token_hex(nbytes=4)}",
            symbol=symbol,
            order_side=close_side,
            quantity=contracts,
            price=None,
            position_side=position_side,
            place_order_behavior=PlaceOrderBehavior.NORMAL,
        )
        logger.info(
            f"平仓 {position_side.value}: 下单方向={close_side.value}, 数量={contracts}, 返回={order}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="BinanceSwapClient demo")
    parser.add_argument("--base", default="BTC", help="交易对基础币, 默认 DOGE")
    parser.add_argument("--quote", default="USDT", help="交易对计价币, 默认 USDT")
    parser.add_argument("--timeframe", default="1m", help="K线周期, 默认 1m")
    parser.add_argument("--quantity", type=float, default=0.001, help="下单数量, 默认 1")
    parser.add_argument(
        "--place-order", action="store_true", help="开启真实下单流程(默认关闭)"
    )
    args = parser.parse_args()

    symbol = Symbol(base=args.base.upper(), quote=args.quote.upper())
    client = create_client()

    logger.info("=== 交易规则 ===")
    demo_symbol_info(client, symbol)

    logger.info("=== 账户余额 ===")
    demo_balance(client)

    logger.info("=== 当前持仓 ===")
    demo_positions(client, symbol)

    logger.info("=== K线数据 ===")
    demo_fetch_ohlcv(client, symbol, args.timeframe)

    if not args.place_order:
        logger.info("未开启 --place-order, 跳过下单相关演示")
        return

    logger.info("=== 下单流程 ===")
    ticker = client.exchange.fetch_ticker(symbol.ccxt())
    last_price = float(ticker["last"])
    logger.info(f"最新价: {last_price}")

    custom_id = demo_place_limit_order(client, symbol, args.quantity, last_price)
    demo_query_order(client, symbol, custom_id)
    demo_cancel_order(client, symbol, custom_id)

    logger.info("=== 市价单与平仓 ===")
    demo_place_market_order(client, symbol, args.quantity)
    demo_close_position(client, symbol)


if __name__ == "__main__":
    main()
