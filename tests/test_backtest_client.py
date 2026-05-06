import pytest
from unittest.mock import MagicMock
from model import Symbol, Kline, OrderSide, PositionSide, OrderStatus
from backtest.client import BacktestClient
from persistence.order_repository import InMemoryOrderRepository
from persistence.kline_data_store import KlineDataStore


SYMBOL = Symbol(base='eth', quote='usdt')
TS_BASE = 1_700_000_000_000


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
    repo = InMemoryOrderRepository()
    client = BacktestClient(order_repo=repo, initial_balance=10_000.0)
    client.update_current_timestamp(TS_BASE)
    client.current_prices[SYMBOL.binance()] = 2000.0
    return client


# ── Timestamp tests ──────────────────────────────────────────────────────────

class TestOrderTimestamp:
    def test_market_order_uses_backtest_timestamp(self):
        client = _client()
        client.update_current_timestamp(TS_BASE + 999)
        order = client.place_order_v2('test', 'o1', SYMBOL, OrderSide.BUY, 1.0,
                                      position_side=PositionSide.LONG)
        assert order is not None
        assert order.created_at == TS_BASE + 999

    def test_limit_order_timestamp_is_placement_time(self):
        client = _client()
        client.update_current_timestamp(TS_BASE + 500)
        order = client.place_order_v2('test', 'o2', SYMBOL, OrderSide.BUY, 1.0,
                                      price=1900.0, position_side=PositionSide.LONG)
        assert order is not None
        assert order.created_at == TS_BASE + 500
        assert order.status == OrderStatus.OPEN


# ── Limit order fill logic ────────────────────────────────────────────────────

class TestLimitOrderFill:
    def test_buy_limit_not_filled_when_price_above_limit(self):
        client = _client()
        client.place_order_v2('test', 'buy1', SYMBOL, OrderSide.BUY, 1.0,
                              price=1900.0, position_side=PositionSide.LONG)
        kline = _make_kline(low=1950.0, high=2050.0, close=2000.0)
        client.check_pending_orders(kline)
        order = client.order_repo.find_by_id('buy1')
        assert order is not None
        assert order.status == OrderStatus.OPEN

    def test_buy_limit_filled_when_low_touches_price(self):
        client = _client()
        client.place_order_v2('test', 'buy2', SYMBOL, OrderSide.BUY, 1.0,
                              price=1900.0, position_side=PositionSide.LONG)
        kline = _make_kline(low=1900.0, high=2050.0, close=2000.0)
        client.check_pending_orders(kline)
        order = client.order_repo.find_by_id('buy2')
        assert order is not None
        assert order.status == OrderStatus.CLOSED
        assert order.filled_price == 1900.0

    def test_buy_limit_filled_when_low_below_price(self):
        client = _client()
        client.place_order_v2('test', 'buy3', SYMBOL, OrderSide.BUY, 1.0,
                              price=1900.0, position_side=PositionSide.LONG)
        kline = _make_kline(low=1850.0, high=2050.0, close=2000.0)
        client.check_pending_orders(kline)
        order = client.order_repo.find_by_id('buy3')
        assert order is not None
        assert order.status == OrderStatus.CLOSED

    def test_sell_limit_not_filled_when_price_below_limit(self):
        client = _client()
        client.place_order_v2('test', 'entry', SYMBOL, OrderSide.BUY, 1.0,
                              position_side=PositionSide.LONG)
        client.place_order_v2('test', 'sell1', SYMBOL, OrderSide.SELL, 1.0,
                              price=2100.0, position_side=PositionSide.LONG)
        kline = _make_kline(low=1950.0, high=2050.0, close=2000.0)
        client.check_pending_orders(kline)
        order = client.order_repo.find_by_id('sell1')
        assert order is not None
        assert order.status == OrderStatus.OPEN

    def test_sell_limit_filled_when_high_touches_price(self):
        client = _client()
        client.place_order_v2('test', 'entry', SYMBOL, OrderSide.BUY, 1.0,
                              position_side=PositionSide.LONG)
        client.place_order_v2('test', 'sell2', SYMBOL, OrderSide.SELL, 1.0,
                              price=2100.0, position_side=PositionSide.LONG)
        kline = _make_kline(low=1950.0, high=2100.0, close=2050.0)
        client.check_pending_orders(kline)
        order = client.order_repo.find_by_id('sell2')
        assert order is not None
        assert order.status == OrderStatus.CLOSED
        assert order.filled_price == 2100.0

    def test_limit_order_filled_at_limit_price_not_market_price(self):
        client = _client()
        client.place_order_v2('test', 'lim', SYMBOL, OrderSide.BUY, 1.0,
                              price=1800.0, position_side=PositionSide.LONG)
        kline = _make_kline(low=1750.0, high=1900.0, close=1850.0)
        client.check_pending_orders(kline)
        order = client.order_repo.find_by_id('lim')
        assert order is not None
        assert order.filled_price == 1800.0


# ── unrealized_pnl ────────────────────────────────────────────────────────────

class TestUnrealizedPnl:
    def test_long_unrealized_pnl_updated(self):
        client = _client()
        client.place_order_v2('test', 'entry', SYMBOL, OrderSide.BUY, 1.0,
                              position_side=PositionSide.LONG)
        kline = _make_kline(low=2190.0, high=2210.0, close=2200.0)
        client.check_pending_orders(kline)
        pos_key = f"{SYMBOL.binance()}_long"
        assert abs(client._positions[pos_key].unrealized_pnl - 200.0) < 0.01

    def test_short_unrealized_pnl_updated(self):
        client = _client()
        client.place_order_v2('test', 'entry', SYMBOL, OrderSide.SELL, 1.0,
                              position_side=PositionSide.SHORT)
        kline = _make_kline(low=1790.0, high=1810.0, close=1800.0)
        client.check_pending_orders(kline)
        pos_key = f"{SYMBOL.binance()}_short"
        assert abs(client._positions[pos_key].unrealized_pnl - 200.0) < 0.01


# ── close_position timestamp ──────────────────────────────────────────────────

class TestClosePosition:
    def test_close_position_uses_backtest_timestamp(self):
        client = _client()
        client.place_order_v2('test', 'entry', SYMBOL, OrderSide.BUY, 1.0,
                              position_side=PositionSide.LONG)
        client.update_current_timestamp(TS_BASE + 12345)
        client.close_position(SYMBOL.binance(), 'long')
        history = client.order_repo.find_history()
        close_order = history[-1]
        assert close_order.created_at == TS_BASE + 12345


# ── SymbolInfo override ───────────────────────────────────────────────────────

class TestSymbolInfo:
    def test_default_symbol_info_fallback(self):
        client = BacktestClient(order_repo=InMemoryOrderRepository())
        info = client.symbol_info(SYMBOL)
        assert info.tick_size == 0.01

    def test_custom_symbol_info_used(self):
        from model import SymbolInfo
        custom = SymbolInfo(
            symbol=SYMBOL, tick_size=0.1, min_price=0.1, max_price=999999.0,
            step_size=0.01, min_qty=0.01, max_qty=10000.0
        )
        client = BacktestClient(order_repo=InMemoryOrderRepository(), symbol_infos={SYMBOL.binance(): custom})
        info = client.symbol_info(SYMBOL)
        assert info.tick_size == 0.1


class TestFetchOhlcvLazyLoading:
    def test_fetch_ohlcv_loads_data_from_store_when_cache_miss(self):
        klines = [_make_kline(low=1900.0, high=2100.0, close=2000.0, ts=TS_BASE + i * 60_000) for i in range(5)]
        mock_store = MagicMock(spec=KlineDataStore)
        mock_store.ensure_data.return_value = "data/mock.csv"
        mock_store.load_csv.return_value = klines

        repo = InMemoryOrderRepository()
        client = BacktestClient(data_store=mock_store, order_repo=repo, initial_balance=10_000.0)
        client.update_current_timestamp(TS_BASE + 5 * 60_000)

        result = client.fetch_ohlcv(SYMBOL, '1m', limit=5)

        mock_store.ensure_data.assert_called_once()
        mock_store.load_csv.assert_called_once_with("data/mock.csv", SYMBOL, '1m')
        assert len(result) == 5

    def test_fetch_ohlcv_uses_cache_on_second_call(self):
        klines = [_make_kline(low=1900.0, high=2100.0, close=2000.0, ts=TS_BASE + i * 60_000) for i in range(5)]
        mock_store = MagicMock(spec=KlineDataStore)
        mock_store.ensure_data.return_value = "data/mock.csv"
        mock_store.load_csv.return_value = klines

        repo = InMemoryOrderRepository()
        client = BacktestClient(data_store=mock_store, order_repo=repo, initial_balance=10_000.0)
        client.update_current_timestamp(TS_BASE + 5 * 60_000)

        client.fetch_ohlcv(SYMBOL, '1m', limit=5)
        client.fetch_ohlcv(SYMBOL, '1m', limit=5)

        mock_store.ensure_data.assert_called_once()
        mock_store.load_csv.assert_called_once()

    def test_fetch_ohlcv_returns_empty_when_no_data_store(self):
        repo = InMemoryOrderRepository()
        client = BacktestClient(order_repo=repo, initial_balance=10_000.0)
        client.update_current_timestamp(TS_BASE)

        result = client.fetch_ohlcv(SYMBOL, '1m', limit=5)
        assert result == []

    def test_fetch_ohlcv_no_lazy_load_when_cache_hit(self):
        klines = [_make_kline(low=1900.0, high=2100.0, close=2000.0, ts=TS_BASE + i * 60_000) for i in range(5)]
        mock_store = MagicMock(spec=KlineDataStore)

        repo = InMemoryOrderRepository()
        client = BacktestClient(data_store=mock_store, order_repo=repo, initial_balance=10_000.0)
        client._store_klines(SYMBOL, '1m', klines)
        client.update_current_timestamp(TS_BASE + 5 * 60_000)

        result = client.fetch_ohlcv(SYMBOL, '1m', limit=5)
        mock_store.ensure_data.assert_not_called()
        assert len(result) == 5
