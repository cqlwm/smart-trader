import ccxt
import pandas as pd
import time

# 初始化 Binance 交易所
api_key = '你的API密钥'
secret = '你的密钥'

binance = ccxt.binance({
    'apiKey': api_key,
    'secret': secret,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future',  # 使用合约交易
    },
})

# 获取市场信息
symbol = 'BTC/USDT'
market = binance.market(symbol)

# 设置参数
initial_amount = 0.001  # 初始开仓数量
price_drop_threshold = 0.002  # 每次价格下跌的阈值 (0.2%)
leverage = 20  # 杠杆倍数


def place_order_and_track(df):
    # 确保账户处于双向持仓模式
    position_mode_info = binance.fapiPrivate_get_positionside_dual()
    if not position_mode_info['dualSidePosition']:
        binance.fapiPrivate_post_positionside_dual({'dualSidePosition': 'true'})
        time.sleep(2)

    initial_price = df['close'].iloc[0]
    current_price = initial_price
    amount = initial_amount
    open_price = initial_price
    total_amount = 0

    while True:
        # 获取最新价格
        ticker = binance.fetch_ticker(symbol)
        last_price = ticker['last']
        print(f"Current Price: {last_price}")

        time.sleep(5)  # 等待5秒钟再检查一次


grids = [1.1, 2, 3.3]


def r(latest_price):
    # 检查价格是否下跌超过阈值
    if latest_price <= grids[-1] * (1 - price_drop_threshold):
        # 市价单加仓
        order = binance.create_order(
            symbol=symbol,
            type='market',
            side='buy',
            amount=amount,
            params={'positionSide': 'LONG'}
        )
        print(f"Added position at price: {last_price}")
        current_price = last_price
        total_amount += amount

        # 设置或更新平仓限价单
        take_profit_price = open_price * 1.002  # 平仓价位为首次开仓价的0.2%上方
        binance.create_order(
            symbol=symbol,
            type='limit',
            side='sell',
            amount=total_amount,
            price=take_profit_price,
            params={'positionSide': 'LONG'}
        )
        print(f"Set take profit order at price: {take_profit_price}")


# 示例使用
ohlcv = [
    [1622476800000, 36000, 36500, 35500, 36200, 1234],
    [1622476860000, 36200, 36300, 35800, 36000, 5678],
    # 添加更多的OHLCV数据
]
df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
place_order_and_track(df)
