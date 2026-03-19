import pytest
from model import Symbol, Kline, OrderSide, PositionSide, OrderStatus
from backtest.backtest_client import BacktestClient


SYMBOL = Symbol(base='eth', quote='usdt')
TS_BASE = 1_700_000_000_000  # arbitrary backtest timestamp


def _make_kline(low: float, high: float, close: float, ts: int = TS_BASE) -> Kline:
    return Kline(
        symbol=SYMBOL,
        timeframe='1m',
        open=close,
        high=high,
        low=low,
        close=close,
        volume=1.0,
        timestamp=ts,
        finished=True,
    )


def _client() -> BacktestClient:
    client = BacktestClient(initial_balance=10_000.0)
    client.update_current_timestamp(TS_BASE)
    client.current_prices[SYMBOL.binance()] = 2000.0
    return client


# ── Timestamp tests ──────────────────────────────────────────────────────────

class TestOrderTimestamp:
    def test_market_order_uses_backtest_timestamp(self):
        client = _client()
        client.update_current_timestamp(TS_BASE + 999)
        client.place_order_v2('o1', SYMBOL, OrderSide.BUY, 1.0,
                              position_side=PositionSide.LONG)
        order = client.order_history[-1]
        assert order.timestamp == TS_BASE + 999

    def test_limit_order_timestamp_is_placement_time(self):
        client = _client()
        client.update_current_timestamp(TS_BASE + 500)
        client.place_order_v2('o2', SYMBOL, OrderSide.BUY, 1.0,
                              price=1900.0, position_side=PositionSide.LONG)
        # Not filled yet — still open, timestamps recorded at placement
        order = client.orders['o2']
        assert order.timestamp == TS_BASE + 500


# ── Limit order fill logic ────────────────────────────────────────────────────

class TestLimitOrderFill:
    def test_buy_limit_not_filled_when_price_above_limit(self):
        """BUY 限价单：kline.low > limit price → 不成交"""
        client = _client()
        client.place_order_v2('buy1', SYMBOL, OrderSide.BUY, 1.0,
                              price=1900.0, position_side=PositionSide.LONG)
        kline = _make_kline(low=1950.0, high=2050.0, close=2000.0)
        client.check_pending_orders(kline)
        assert client.orders['buy1'].status == OrderStatus.OPEN

    def test_buy_limit_filled_when_low_touches_price(self):
        """BUY 限价单：kline.low <= limit price → 成交"""
        client = _client()
        client.place_order_v2('buy2', SYMBOL, OrderSide.BUY, 1.0,
                              price=1900.0, position_side=PositionSide.LONG)
        kline = _make_kline(low=1900.0, high=2050.0, close=2000.0)
        client.check_pending_orders(kline)
        assert client.orders['buy2'].status == OrderStatus.CLOSED
        assert client.order_history[-1].filled_price == 1900.0

    def test_buy_limit_filled_when_low_below_price(self):
        """BUY 限价单：kline.low < limit price → 成交"""
        client = _client()
        client.place_order_v2('buy3', SYMBOL, OrderSide.BUY, 1.0,
                              price=1900.0, position_side=PositionSide.LONG)
        kline = _make_kline(low=1850.0, high=2050.0, close=2000.0)
        client.check_pending_orders(kline)
        assert client.orders['buy3'].status == OrderStatus.CLOSED

    def test_sell_limit_not_filled_when_price_below_limit(self):
        """SELL 限价单：kline.high < limit price → 不成交"""
        client = _client()
        # First open a long position so we can sell
        client.place_order_v2('entry', SYMBOL, OrderSide.BUY, 1.0,
                              position_side=PositionSide.LONG)
        client.place_order_v2('sell1', SYMBOL, OrderSide.SELL, 1.0,
                              price=2100.0, position_side=PositionSide.LONG)
        kline = _make_kline(low=1950.0, high=2050.0, close=2000.0)
        client.check_pending_orders(kline)
        assert client.orders['sell1'].status == OrderStatus.OPEN

    def test_sell_limit_filled_when_high_touches_price(self):
        """SELL 限价单：kline.high >= limit price → 成交"""
        client = _client()
        client.place_order_v2('entry', SYMBOL, OrderSide.BUY, 1.0,
                              position_side=PositionSide.LONG)
        client.place_order_v2('sell2', SYMBOL, OrderSide.SELL, 1.0,
                              price=2100.0, position_side=PositionSide.LONG)
        kline = _make_kline(low=1950.0, high=2100.0, close=2050.0)
        client.check_pending_orders(kline)
        assert client.orders['sell2'].status == OrderStatus.CLOSED
        assert client.order_history[-1].filled_price == 2100.0

    def test_limit_order_filled_at_limit_price_not_market_price(self):
        """限价单成交价应为限价，而非市价"""
        client = _client()
        client.place_order_v2('lim', SYMBOL, OrderSide.BUY, 1.0,
                              price=1800.0, position_side=PositionSide.LONG)
        kline = _make_kline(low=1750.0, high=1900.0, close=1850.0)
        client.check_pending_orders(kline)
        filled = next(o for o in client.order_history if o.custom_id == 'lim')
        assert filled.filled_price == 1800.0


# ── unrealized_pnl ────────────────────────────────────────────────────────────

class TestUnrealizedPnl:
    def test_long_unrealized_pnl_updated(self):
        client = _client()
        client.place_order_v2('entry', SYMBOL, OrderSide.BUY, 1.0,
                              position_side=PositionSide.LONG)
        # entry at 2000, price moves to 2200
        kline = _make_kline(low=2190.0, high=2210.0, close=2200.0)
        client.check_pending_orders(kline)
        pos_key = f"{SYMBOL.binance()}_long"
        assert abs(client._positions[pos_key].unrealized_pnl - 200.0) < 0.01

    def test_short_unrealized_pnl_updated(self):
        client = _client()
        client.place_order_v2('entry', SYMBOL, OrderSide.SELL, 1.0,
                              position_side=PositionSide.SHORT)
        # entry at 2000, price drops to 1800 → profit for short
        kline = _make_kline(low=1790.0, high=1810.0, close=1800.0)
        client.check_pending_orders(kline)
        pos_key = f"{SYMBOL.binance()}_short"
        assert abs(client._positions[pos_key].unrealized_pnl - 200.0) < 0.01


# ── close_position timestamp ──────────────────────────────────────────────────

class TestClosePosition:
    def test_close_position_uses_backtest_timestamp(self):
        client = _client()
        client.place_order_v2('entry', SYMBOL, OrderSide.BUY, 1.0,
                              position_side=PositionSide.LONG)
        client.update_current_timestamp(TS_BASE + 12345)
        client.close_position(SYMBOL.binance(), 'long')
        close_order = client.order_history[-1]
        assert close_order.timestamp == TS_BASE + 12345


# ── SymbolInfo override ───────────────────────────────────────────────────────

class TestSymbolInfo:
    def test_default_symbol_info_fallback(self):
        client = BacktestClient()
        info = client.symbol_info(SYMBOL)
        assert info.tick_size == 0.01

    def test_custom_symbol_info_used(self):
        from model import SymbolInfo
        custom = SymbolInfo(
            symbol=SYMBOL, tick_size=0.1, min_price=0.1, max_price=999999.0,
            step_size=0.01, min_qty=0.01, max_qty=10000.0
        )
        client = BacktestClient(symbol_infos={SYMBOL.binance(): custom})
        info = client.symbol_info(SYMBOL)
        assert info.tick_size == 0.1
