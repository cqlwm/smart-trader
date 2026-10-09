"""探测 ccxt 订单响应是否始终回传 side

目的: 决定适配器能否对"交易所未回传方向"直接抛异常。
      本脚本直接调用 ccxt, 打印 create_order / fetch_order / cancel_order 的
      归一化订单结构, 重点检查 `side` 字段是否存在且为 buy/sell。

运行:
    uv run python demos/order_side_probe_demo.py
    uv run python demos/order_side_probe_demo.py --base ETH --quantity 0.02

凭证(与 run.py 一致, 从环境变量 / .env 读取):
    BINANCE_API_KEY_MAIN / BINANCE_API_SECRET_MAIN / BINANCE_IS_TEST_MAIN

注意: 会产生委托; 挂单与市价单开出的仓位会在最后自动清理。
"""

import argparse
import contextlib
import os
import secrets
from typing import Any, cast

import ccxt  # pyright: ignore[reportMissingTypeStubs]
import dotenv
from ccxt.base.types import ConstructorArgs  # pyright: ignore[reportMissingTypeStubs]

from smart_trader import log

dotenv.load_dotenv()
log.init_logging()
logger = log.getLogger(__name__)

# 每个场景下 side 字段的观测结果: (场景, 原始 side 值)
OBSERVATIONS: list[tuple[str, Any]] = []


def build_exchange() -> Any:
    """按 BinanceSwapClient 的配置构建 ccxt 合约交易所"""
    api_key = os.environ.get("BINANCE_API_KEY_MAIN")
    api_secret = os.environ.get("BINANCE_API_SECRET_MAIN")
    is_test = os.environ.get("BINANCE_IS_TEST_MAIN") == "True"
    if not api_key or not api_secret:
        raise ValueError("BINANCE_API_KEY_MAIN / BINANCE_API_SECRET_MAIN 未设置")

    logger.info("is_test(demo trading): %s", is_test)
    exchange: Any = ccxt.binance(
        ConstructorArgs(
            apiKey=api_key,
            secret=api_secret,
            options={
                "defaultType": "future",
                "adjustForTimeDifference": True,
            },
        )
    )
    if is_test:
        exchange.enable_demo_trading(True)
    exchange.load_markets()
    return exchange


def probe(case: str, order: Any) -> None:
    """记录一次响应中 side 字段的情况"""
    payload = cast("dict[str, Any]", order)
    side: Any = payload.get("side")
    valid = isinstance(side, str) and side.lower() in ("buy", "sell")
    OBSERVATIONS.append((case, side))
    logger.info("[%s] side=%r, 合法=%s", case, side, valid)
    logger.info("    完整响应: %s", payload)


def cleanup(exchange: Any, symbol: str, limit_id: str, amount: float) -> None:
    """撤销残余挂单, 并用反向市价单平掉探测仓位"""
    if limit_id:
        with contextlib.suppress(Exception):
            exchange.cancel_order(
                "", symbol, {"origClientOrderId": limit_id}
            )
            logger.info("已撤销残余挂单: %s", limit_id)

    for position in exchange.fetch_positions([symbol]):
        contracts = float(position.get("contracts") or 0.0)
        if contracts <= 0:
            continue
        close_side = "sell" if position.get("side") == "long" else "buy"
        logger.info("平掉探测仓位: side=%s, 数量=%s", close_side, contracts)
        with contextlib.suppress(Exception):
            exchange.create_order(
                symbol,
                "market",
                close_side,
                contracts,
                None,
                {"positionSide": str(position.get("side") or "LONG").upper()},
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="ccxt 订单 side 字段探测")
    parser.add_argument("--base", default="BTC", help="基础币, 默认 BTC")
    parser.add_argument("--quote", default="USDT", help="计价币, 默认 USDT")
    parser.add_argument("--quantity", type=float, default=0.002, help="下单数量")
    args = parser.parse_args()

    quote = args.quote.upper()
    symbol = f"{args.base.upper()}/{quote}:{quote}"
    exchange = build_exchange()

    market = exchange.market(symbol)
    if not market.get("swap"):
        raise ValueError(f"{symbol} 不是合约市场: type={market.get('type')}")

    amount = float(exchange.amount_to_precision(symbol, args.quantity))
    last = float(exchange.fetch_ticker(symbol)["last"])
    # 买单挂在市价下方, 保证 post-only 挂单成交不了
    price = float(exchange.price_to_precision(symbol, last * 0.9))
    logger.info("symbol=%s, last=%s, 探测价=%s, 数量=%s", symbol, last, price, amount)

    limit_id = ""
    try:
        logger.info("=== 场景1: 限价挂单 create_order(open) ===")
        limit_id = f"probe{secrets.token_hex(5)}"
        created = exchange.create_order(
            symbol,
            "limit",
            "buy",
            amount,
            price,
            {
                "newClientOrderId": limit_id,
                "positionSide": "LONG",
                "timeInForce": "GTX",
            },
        )
        probe("create_order(limit, open)", created)

        logger.info("=== 场景2: 查询挂单 fetch_order(open) ===")
        probe(
            "fetch_order(open)",
            exchange.fetch_order("", symbol, {"origClientOrderId": limit_id}),
        )

        logger.info("=== 场景3: 撤单 cancel_order(canceled) ===")
        canceled = exchange.cancel_order(
            "", symbol, {"origClientOrderId": limit_id}
        )
        probe("cancel_order(canceled)", canceled)
        canceled_id = str(canceled.get("clientOrderId") or limit_id)
        limit_id = ""

        logger.info("=== 场景4: 查询已撤销单 fetch_order(canceled) ===")
        probe(
            "fetch_order(canceled)",
            exchange.fetch_order("", symbol, {"origClientOrderId": canceled_id}),
        )

        logger.info("=== 场景5: 市价成交 create_order(closed) ===")
        market_id = f"probe{secrets.token_hex(5)}"
        filled = exchange.create_order(
            symbol,
            "market",
            "buy",
            amount,
            None,
            {"newClientOrderId": market_id, "positionSide": "LONG"},
        )
        probe("create_order(market, closed)", filled)

        logger.info("=== 场景6: 查询已成交单 fetch_order(closed) ===")
        probe(
            "fetch_order(closed)",
            exchange.fetch_order("", symbol, {"origClientOrderId": market_id}),
        )
    finally:
        cleanup(exchange, symbol, limit_id, amount)

    logger.info("=== 汇总 ===")
    all_present = True
    for case, side in OBSERVATIONS:
        valid = isinstance(side, str) and side.lower() in ("buy", "sell")
        all_present = all_present and valid
        logger.info("%-30s side=%-7r %s", case, side, "OK" if valid else "缺失/非法!!")
    logger.info("结论: 所有场景 side 均回传=%s", all_present)


if __name__ == "__main__":
    main()