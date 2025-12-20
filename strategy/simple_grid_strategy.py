import numpy as np
import time
from typing import List, Dict, Literal
import pandas as pd
from datetime import datetime
import os
from strategy import SingleTimeframeStrategy
from client.ex_client import ExSwapClient
from model import Symbol, Kline, OrderSide, OrderStatus
import log

logger = log.getLogger(__name__)

class SimpleGridStrategy(SingleTimeframeStrategy):
    def __init__(self, ex_client: ExSwapClient, symbol: Symbol,
                 upper_price: float, lower_price: float, grid_num: int,
                 quantity_per_grid: float, position_side: Literal['LONG', 'SHORT'], timeframe: str):
        super().__init__(timeframe)
        self.ex_client = ex_client
        self.symbol = symbol
        self.upper_price = upper_price
        self.lower_price = lower_price
        self.grid_num = grid_num
        self.quantity = quantity_per_grid
        self.position_side = position_side
        self.grid_prices = self._calculate_grid_prices()
        self.active_orders: Dict[str, dict] = {}
        self.positions: Dict[float, Dict[str, float]] = {}  # 价格 -> {quantity: float, price: float}
        self.trade_pairs = 0  # 配对交易次数
        self.total_profit = 0.0  # 总盈利
        self.trade_history: List[dict] = []  # 交易历史
        self.orders_df = pd.DataFrame(columns=['时间', '价格', '方向', '数量', '状态'])
        self.orders_file = f'grid_orders_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        self.update_orders_csv()

    def _calculate_grid_prices(self) -> List[float]:
        """计算网格价格"""
        return list(np.linspace(self.lower_price, self.upper_price, self.grid_num))

    def get_current_price(self) -> float:
        """获取当前市场价格"""
        if self.latest_kline_obj:
            return self.latest_kline_obj.close
        # 如果没有K线数据，使用API获取
        ticker = self.ex_client.fetch_ticker(self.symbol)
        return float(ticker['last'])

    def _place_order(self, price: float, quantity: float, side: OrderSide):
        """下单函数"""
        try:
            order = self.ex_client.create_limit_order(
                symbol=self.symbol,
                side=side,
                amount=quantity,
                price=price
            )
            self.active_orders[order['id']] = {
                'price': price,
                'side': side,
                'quantity': quantity
            }
            # 记录订单 - 使用 loc 方法安全地添加新行
            self.orders_df.loc[len(self.orders_df)] = {
                '时间': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                '价格': price,
                '方向': side.value,
                '数量': quantity,
                '状态': '未成交'
            }
            self.update_orders_csv()
            logger.info(f"下单成功: {side.value} {quantity} @ {price}")
        except Exception as e:
            logger.error(f"下单错误: {e}")

    def handle_filled_order(self, order_id: str):
        """处理已成交订单"""
        if order_id not in self.active_orders:
            return

        filled_order = self.active_orders[order_id]
        price = filled_order['price']
        side = filled_order['side']
        quantity = filled_order['quantity']

        # 更新订单状态
        mask = (self.orders_df['价格'] == price) & (self.orders_df['方向'] == side.value)
        self.orders_df.loc[mask, '状态'] = '已成交'
        self.update_orders_csv()

        # 移除已成交订单
        del self.active_orders[order_id]

        # 更新持仓和计算盈利
        if self.position_side == 'LONG':
            if side == OrderSide.BUY:
                # 开仓做多
                self.positions[price] = {'quantity': quantity, 'price': price}
                # 在上方网格位置开平仓单
                next_price = min([p for p in self.grid_prices if p > price], default=None)
                if next_price:
                    self._place_order(next_price, quantity, OrderSide.SELL)
            else:  # OrderSide.SELL
                # 平仓多头，计算盈利
                for open_price in sorted(self.positions.keys()):
                    if self.positions[open_price]['quantity'] > 0:
                        self.trade_pairs += 1
                        profit = (price - open_price) * quantity
                        self.total_profit += profit
                        logger.info(f"配对交易次数: {self.trade_pairs}, 开仓价: {open_price}, 平仓价: {price}, 本次盈利: {profit:.4f}, 总盈利: {self.total_profit:.4f}")
                        self.positions[open_price]['quantity'] = 0
                        break
                # 在下方网格位置开新的多单
                next_price = max([p for p in self.grid_prices if p < price], default=None)
                if next_price:
                    self._place_order(next_price, quantity, OrderSide.BUY)
        else:  # SHORT
            if side == OrderSide.SELL:
                # 开仓做空
                self.positions[price] = {'quantity': quantity, 'price': price}
                # 在下方网格位置开平仓单
                next_price = max([p for p in self.grid_prices if p < price], default=None)
                if next_price:
                    self._place_order(next_price, quantity, OrderSide.BUY)
            else:  # OrderSide.BUY
                # 平仓空头，计算盈利
                for open_price in sorted(self.positions.keys(), reverse=True):
                    if self.positions[open_price]['quantity'] > 0:
                        self.trade_pairs += 1
                        profit = (open_price - price) * quantity
                        self.total_profit += profit
                        logger.info(f"配对交易次数: {self.trade_pairs}, 开仓价: {open_price}, 平仓价: {price}, 本次盈利: {profit:.4f}, 总盈利: {self.total_profit:.4f}")
                        self.positions[open_price]['quantity'] = 0
                        break
                # 在上方网格位置开新的空单
                next_price = min([p for p in self.grid_prices if p > price], default=None)
                if next_price:
                    self._place_order(next_price, quantity, OrderSide.SELL)

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
                    self._place_order(price, self.quantity, OrderSide.BUY)
                elif price > current_price:
                    # 上方网格布置卖单（平多）
                    self._place_order(price, self.quantity, OrderSide.SELL)
            else:  # SHORT
                if price < current_price:
                    # 下方网格布置买单（平空）
                    self._place_order(price, self.quantity, OrderSide.BUY)
                elif price > current_price:
                    # 上方网格布置卖单（开空）
                    self._place_order(price, self.quantity, OrderSide.SELL)

    def run_strategy(self):
        """运行策略 - 独立运行方法"""
        logger.info("启动网格交易策略...")
        logger.info(f"订单记录将保存在: {self.orders_file}")
        self.place_grid_orders()
        
        while True:
            try:
                # 检查订单状态
                for order_id in list(self.active_orders.keys()):
                    order = self.ex_client.fetch_order(order_id, self.symbol)
                    if order['status'] == 'closed':
                        self.handle_filled_order(order_id)
                
                # 每10秒打印一次统计信息
                if int(time.time()) % 10 == 0:
                    logger.info(f"当前统计 - 配对交易次数: {self.trade_pairs}, 总盈利: {self.total_profit:.4f}")
                
                time.sleep(1)
            except Exception as e:
                logger.error(f"运行错误: {e}")
                time.sleep(5)

    # 实现SingleTimeframeStrategy的抽象方法
    def _on_kline(self):
        """每次K线更新时调用"""
        pass

    def _on_kline_finished(self):
        """K线完成时调用，可以在这里处理网格订单逻辑"""
        pass


def main():
    """
    使用新架构的示例用法
    可以直接运行SimpleGridStrategy，也可以集成到事件循环系统中
    """
    # 从环境变量读取 API 密钥
    api_key = os.environ.get('BINANCE_API_KEY', '')
    api_secret = os.environ.get('BINANCE_API_SECRET', '')
    is_test = os.environ.get('BINANCE_IS_TEST') == 'True'
    
    if not api_key or not api_secret:
        raise ValueError("请设置环境变量 BINANCE_API_KEY 和 BINANCE_API_SECRET")

    # 导入并创建客户端
    from client.binance_client import BinanceSwapClient
    
    binance_client = BinanceSwapClient(
        api_key=api_key,
        api_secret=api_secret,
        is_test=is_test,
    )

    # 策略参数
    symbol = Symbol(base="DOGE", quote="USDC")
    upper_price = 0.239093
    lower_price = 0.201515 * 0.995
    grid_num = 25
    quantity = 28
    position_side = 'SHORT'  # 'LONG' 或 'SHORT'

    strategy = SimpleGridStrategy(
        ex_client=binance_client,
        symbol=symbol,
        upper_price=upper_price,
        lower_price=lower_price,
        grid_num=grid_num,
        quantity_per_grid=quantity,
        position_side=position_side,
        timeframe='1m'
    )
    
    # 运行策略
    strategy.run_strategy()


def create_strategy_for_event_loop(binance_client: ExSwapClient, symbol: Symbol,
                                 upper_price: float, lower_price: float,
                                 grid_num: int, quantity: float,
                                 position_side: Literal['LONG', 'SHORT'], timeframe: str = '1m') -> SimpleGridStrategy:
    """
    创建用于事件循环系统的策略实例
    可以在run.py中调用此函数来集成到主系统中
    """
    return SimpleGridStrategy(
        ex_client=binance_client,
        symbol=symbol,
        upper_price=upper_price,
        lower_price=lower_price,
        grid_num=grid_num,
        quantity_per_grid=quantity,
        position_side=position_side,
        timeframe=timeframe
    )

if __name__ == "__main__":
    main()
