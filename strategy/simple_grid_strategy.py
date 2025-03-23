from binance.client import Client
from binance.enums import ORDER_TYPE_LIMIT, TIME_IN_FORCE_GTC, SIDE_BUY, SIDE_SELL
import numpy as np
import time
from typing import List, Dict, Literal
import pandas as pd
from datetime import datetime
import os

class SimpleGridStrategy:
    def __init__(self, api_key: str, api_secret: str, symbol: str,
                 upper_price: float, lower_price: float, grid_num: int,
                 quantity_per_grid: float, position_side: Literal['LONG', 'SHORT']):
        self.client = Client(api_key, api_secret)
        self.symbol = symbol
        self.upper_price = upper_price
        self.lower_price = lower_price
        self.grid_num = grid_num
        self.quantity = quantity_per_grid
        self.position_side = position_side
        self.grid_prices = self._calculate_grid_prices()
        self.active_orders: Dict[str, dict] = {}
        self.positions: Dict[str, float] = {}
        self.trade_pairs = 0  # 配对交易次数
        self.total_profit = 0.0  # 总盈利
        self.trade_history = []  # 交易历史
        self.orders_df = pd.DataFrame(columns=['时间', '价格', '方向', '数量', '状态'])
        self.orders_file = f'grid_orders_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        self.update_orders_csv()

    def _calculate_grid_prices(self) -> List[float]:
        """计算网格价格"""
        return list(np.linspace(self.lower_price, self.upper_price, self.grid_num))

    def get_current_price(self) -> float:
        """获取当前市场价格"""
        ticker = self.client.futures_symbol_ticker(symbol=self.symbol)
        return float(ticker['price'])

    def _place_order(self, price: float, quantity: float, side: str):
        """下单函数"""
        try:
            order = self.client.futures_create_order(
                symbol=self.symbol,
                side=side,
                type=ORDER_TYPE_LIMIT,
                timeInForce=TIME_IN_FORCE_GTC,
                quantity=quantity,
                price=round(price, 5),
                positionSide=self.position_side
            )
            self.active_orders[order['orderId']] = {
                'price': price,
                'side': side,
                'quantity': quantity
            }
            # 记录订单 - 使用 loc 方法安全地添加新行
            self.orders_df.loc[len(self.orders_df)] = {
                '时间': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                '价格': price,
                '方向': side,
                '数量': quantity,
                '状态': '未成交'
            }
            self.update_orders_csv()
        except Exception as e:
            print(f"下单错误: {e}")

    def handle_filled_order(self, order_id: str):
        """处理已成交订单"""
        if order_id not in self.active_orders:
            return

        filled_order = self.active_orders[order_id]
        price = filled_order['price']
        side = filled_order['side']
        quantity = filled_order['quantity']

        # 更新订单状态
        mask = (self.orders_df['价格'] == price) & (self.orders_df['方向'] == side)
        self.orders_df.loc[mask, '状态'] = '已成交'
        self.update_orders_csv()

        # 移除已成交订单
        del self.active_orders[order_id]

        # 更新持仓和计算盈利
        if self.position_side == 'LONG':
            if side == SIDE_BUY:
                # 开仓做多
                self.positions[price] = {'quantity': quantity, 'price': price}
                # 在上方网格位置开平仓单
                next_price = min([p for p in self.grid_prices if p > price], default=None)
                if next_price:
                    self._place_order(next_price, quantity, SIDE_SELL)
            else:  # SIDE_SELL
                # 平仓多头，计算盈利
                for open_price in sorted(self.positions.keys()):
                    if self.positions[open_price]['quantity'] > 0:
                        self.trade_pairs += 1
                        profit = (price - open_price) * quantity
                        self.total_profit += profit
                        print(f"配对交易次数: {self.trade_pairs}, 开仓价: {open_price}, 平仓价: {price}, 本次盈利: {profit:.4f}, 总盈利: {self.total_profit:.4f}")
                        self.positions[open_price]['quantity'] = 0
                        break
                # 在下方网格位置开新的多单
                next_price = max([p for p in self.grid_prices if p < price], default=None)
                if next_price:
                    self._place_order(next_price, quantity, SIDE_BUY)
        else:  # SHORT
            if side == SIDE_SELL:
                # 开仓做空
                self.positions[price] = {'quantity': quantity, 'price': price}
                # 在下方网格位置开平仓单
                next_price = max([p for p in self.grid_prices if p < price], default=None)
                if next_price:
                    self._place_order(next_price, quantity, SIDE_BUY)
            else:  # SIDE_BUY
                # 平仓空头，计算盈利
                for open_price in sorted(self.positions.keys(), reverse=True):
                    if self.positions[open_price]['quantity'] > 0:
                        self.trade_pairs += 1
                        profit = (open_price - price) * quantity
                        self.total_profit += profit
                        print(f"配对交易次数: {self.trade_pairs}, 开仓价: {open_price}, 平仓价: {price}, 本次盈利: {profit:.4f}, 总盈利: {self.total_profit:.4f}")
                        self.positions[open_price]['quantity'] = 0
                        break
                # 在上方网格位置开新的空单
                next_price = min([p for p in self.grid_prices if p > price], default=None)
                if next_price:
                    self._place_order(next_price, quantity, SIDE_SELL)

    def update_orders_csv(self):
        """更新CSV文件"""
        try:
            self.orders_df.to_csv(self.orders_file, index=False, encoding='utf-8-sig')
        except Exception as e:
            print(f"保存CSV文件错误: {e}")

    def place_grid_orders(self):
        """布置网格订单"""
        current_price = self.get_current_price()
        
        for price in self.grid_prices:
            if self.position_side == 'LONG':
                if price < current_price:
                    # 下方网格布置买单（开多）
                    self._place_order(price, self.quantity, SIDE_BUY)
                elif price > current_price:
                    # 上方网格布置卖单（平多）
                    self._place_order(price, self.quantity, SIDE_SELL)
            else:  # SHORT
                if price < current_price:
                    # 下方网格布置买单（平空）
                    self._place_order(price, self.quantity, SIDE_BUY)
                elif price > current_price:
                    # 上方网格布置卖单（开空）
                    self._place_order(price, self.quantity, SIDE_SELL)

    def run(self):
        """运行策略"""
        print("启动网格交易策略...")
        print(f"订单记录将保存在: {self.orders_file}")
        self.place_grid_orders()
        
        while True:
            try:
                # 检查订单状态
                for order_id in list(self.active_orders.keys()):
                    order = self.client.futures_get_order(
                        symbol=self.symbol,
                        orderId=order_id
                    )
                    if order['status'] == 'FILLED':
                        self.handle_filled_order(order_id)
                
                # 每10秒打印一次统计信息
                if int(time.time()) % 10 == 0:
                    print(f"当前统计 - 配对交易次数: {self.trade_pairs}, 总盈利: {self.total_profit:.4f}")
                
                time.sleep(1)
            except Exception as e:
                print(f"运行错误: {e}")
                time.sleep(5)


def main():
    # 从环境变量读取 API 密钥
    API_KEY = os.getenv('BINANCE_API_KEY', '')
    API_SECRET = os.getenv('BINANCE_API_SECRET', '')
    
    if not API_KEY or not API_SECRET:
        raise ValueError("请设置环境变量 BINANCE_API_KEY 和 BINANCE_API_SECRET")

    # 策略参数
    SYMBOL = "DOGEUSDC"
    UPPER_PRICE = 0.239093
    LOWER_PRICE = 0.201515 * 0.995
    GRID_NUM = 25
    QUANTITY = 28
    POSITION_SIDE = 'SHORT'# 'LONG' 或 'SHORT'

    strategy = SimpleGridStrategy(
        API_KEY, API_SECRET, SYMBOL,
        UPPER_PRICE, LOWER_PRICE, GRID_NUM, QUANTITY,
        POSITION_SIDE
    )
    strategy.run()

if __name__ == "__main__":
    main()