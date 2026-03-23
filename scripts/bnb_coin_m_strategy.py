import os
import logging
from typing import TypeAlias, Any

import ccxt

import dotenv

dotenv.load_dotenv()

# 配置日志
logging.basicConfig(
    format="%(asctime)s UTC %(levelname)s %(module)s.%(funcName)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 类型别名
PositionData: TypeAlias = dict[str, Any]

def init_exchange() -> ccxt.binance:
    """初始化币安客户端，支持币本位合约。"""
    api_key = os.getenv("BINANCE_API_KEY_MAIN")
    secret_key = os.getenv("BINANCE_API_SECRET_MAIN")
    is_test = os.getenv("BINANCE_IS_TEST_MAIN") == "True"
    
    if not api_key or not secret_key:
        logger.warning("未检测到 API 密钥，可能会影响需要鉴权的接口调用。")
    
    exchange = ccxt.binance({
        "apiKey": api_key,
        "secret": secret_key,
        "enableRateLimit": True,
        "options": {
            "defaultType": "delivery",  # 币安币本位合约
            "defaultSubType": "inverse"
        }
    })
    
    if is_test:
        exchange.set_sandbox_mode(True)
    
    return exchange

def get_yesterday_trend(exchange: ccxt.binance, symbol: str) -> tuple[str, float]:
    """
    判断昨日的 K 线涨跌，并获取今日开盘价。
    返回 ('UP', today_open) 表示上涨（收盘 >= 开盘），返回 ('DOWN', today_open) 表示下跌（收盘 < 开盘）。
    """
    try:
        # 获取最近两天的日线数据 (0: 昨天, 1: 今天未走完)
        # ccxt 返回的 OHLCV: [timestamp, open, high, low, close, volume]
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe="1d", limit=2)
        if len(ohlcv) < 2:
            logger.error("获取到的 K 线数据不足 2 根")
            raise ValueError("K 线数据不足")
            
        yesterday_candle = ohlcv[-2]
        
        open_price = yesterday_candle[1]
        close_price = yesterday_candle[4]
        
        if close_price >= open_price:
            logger.info(f"{symbol} 昨日行情上涨 (开盘: {open_price}, 收盘: {close_price})")
            return "UP", close_price
        else:
            logger.info(f"{symbol} 昨日行情下跌 (开盘: {open_price}, 收盘: {close_price})")
            return "DOWN", close_price
    except ccxt.NetworkError as e:
        logger.error(f"网络异常，无法获取 K 线数据: {e}")
        raise
    except ccxt.ExchangeError as e:
        logger.error(f"交易所异常，无法获取 K 线数据: {e}")
        raise

def get_positions(exchange: ccxt.binance, symbol: str) -> tuple[PositionData | None, PositionData | None]:
    """
    获取指定交易对的多头和空头仓位信息。
    返回 (long_position, short_position)，如果没有对应仓位则返回 None。
    """
    try:
        # fetch_positions 会返回账户的所有合约仓位
        positions = exchange.fetch_positions([symbol])
        
        long_pos = None
        short_pos = None
        
        for pos in positions:
            # 获取仓位方向和数量
            side = pos.get("side")
            contracts = float(pos.get("contracts", 0.0))
            
            # 只有数量大于 0 才认为持有仓位
            if contracts > 0:
                if side == "long":
                    long_pos = pos
                elif side == "short":
                    short_pos = pos
                    
        return long_pos, short_pos
    except ccxt.NetworkError as e:
        logger.error(f"网络异常，无法获取仓位数据: {e}")
        raise
    except ccxt.ExchangeError as e:
        logger.error(f"交易所异常，无法获取仓位数据: {e}")
        raise

def execute_strategy(exchange: ccxt.binance, symbol: str, amount: int = 1):
    """执行核心交易策略。"""
    try:
        trend, close_price = get_yesterday_trend(exchange, symbol)
        long_pos, short_pos = get_positions(exchange, symbol)
                
        if trend == "DOWN":
            # 昨天下跌
            if short_pos and float(short_pos.get("unrealizedPnl", 0.0)) > 0:
                logger.info(f"昨天下跌，空头仓位处于盈利状态，执行平空 {amount} 张合约 (限价: {close_price})。")
                exchange.create_order(
                    symbol, "limit", "buy", amount, price=close_price,
                    params={"positionSide": "SHORT"}
                )
            else:
                logger.info(f"昨天下跌，空头未盈利或无空头仓位，执行开多 {amount} 张合约 (限价: {close_price})。")
                exchange.create_order(
                    symbol, "limit", "buy", amount, price=close_price,
                    params={"positionSide": "LONG"}
                )
                
        elif trend == "UP":
            # 昨天上涨
            if long_pos and float(long_pos.get("unrealizedPnl", 0.0)) > 0:
                logger.info(f"昨天上涨，多头仓位处于盈利状态，执行平多 {amount} 张合约 (限价: {close_price})。")
                exchange.create_order(
                    symbol, "limit", "sell", amount, price=close_price,
                    params={"positionSide": "LONG"}
                )
            else:
                logger.info(f"昨天上涨，多头未盈利或无多头仓位，执行开空 {amount} 张合约 (限价: {close_price})。")
                exchange.create_order(
                    symbol, "limit", "sell", amount, price=close_price,
                    params={"positionSide": "SHORT"}
                )
                
    except ccxt.BaseError as e:
        logger.error(f"执行策略时发生 CCXT 异常: {e}")
    except Exception as e:
        logger.error(f"执行策略时发生未知异常: {e}")

def main() -> None:
    # BNB 币本位永续合约标识符 (Base/Quote:Settle)
    symbol = "BNB/USD:BNB"
    logger.info(f"开始执行币安币本位合约策略，交易标的: {symbol}")
    
    exchange = init_exchange()
    execute_strategy(exchange, symbol, amount=1)
    
    logger.info("策略执行完毕。")

if __name__ == "__main__":
    main()
