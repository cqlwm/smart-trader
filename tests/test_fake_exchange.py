"""StatefulFakeExchange 契约测试: 订单簿语义 + 故障注入 + 持久化

状态化交易所是集成测试的"交易所事实"来源, 与回放式 FakeTradingPort 的区别:
place 记账入簿、query/cancel 读簿、跨策略重建(模拟重启)存活。
"""

from pathlib import Path

import pytest

from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.model import (
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)
from tests.fake_exchange import StatefulFakeExchange
from tests.fakes import DEFAULT_SYMBOL


def make_request(
    custom_id: str = "BUYaaa111",
    side: OrderSide = OrderSide.BUY,
    quantity: float = 100.0,
    price: float | None = 0.5,
) -> OrderRequest:
    """构造一笔限价买入请求"""
    return OrderRequest(
        custom_id=custom_id,
        symbol=DEFAULT_SYMBOL,
        side=side,
        type=OrderType.LIMIT,
        quantity=quantity,
        price=price,
        position_side=PositionSide.LONG,
    )


class TestOrderBookSemantics:
    """订单簿语义: place 入簿, query/cancel 读簿"""

    def test_place_records_order_and_returns_open(self) -> None:
        exchange = StatefulFakeExchange()

        result = exchange.place(make_request())

        assert result.status is OrderStatus.OPEN, f"新单应挂上, 但状态是 {result.status}"
        assert result.custom_id == "BUYaaa111", "应回传请求的 custom_id"
        assert result.filled_qty == 0.0, "新单不应有成交量"
        assert "BUYaaa111" in exchange.book, "place 后订单必须入簿"

    def test_query_reflects_book_state(self) -> None:
        exchange = StatefulFakeExchange()
        exchange.place(make_request())
        exchange.fill("BUYaaa111", 100.0, avg_price=0.5)

        result = exchange.query("BUYaaa111", DEFAULT_SYMBOL)

        assert result is not None, "簿内订单必须查得到"
        assert result.status is OrderStatus.CLOSED, "脚本成交后查询应回报 CLOSED"
        assert result.filled_qty == 100.0, f"应回报全部成交量, 但 got {result.filled_qty}"
        assert result.avg_price == 0.5, "应回报成交均价"

    def test_query_unknown_returns_none(self) -> None:
        exchange = StatefulFakeExchange()

        assert exchange.query("BUYmissing", DEFAULT_SYMBOL) is None, (
            "交易所从未存在过的订单应返回 None(适配器把 -2011 翻译为 None)"
        )

    def test_cancel_marks_canceled_and_keeps_fill(self) -> None:
        exchange = StatefulFakeExchange()
        exchange.place(make_request())
        exchange.fill("BUYaaa111", 30.0, avg_price=0.5, status=OrderStatus.OPEN)

        result = exchange.cancel("BUYaaa111", DEFAULT_SYMBOL)

        assert result is not None, "簿内订单可撤"
        assert result.status is OrderStatus.CANCELED, "撤单后应回报 CANCELED"
        assert result.filled_qty == 30.0, "撤单必须保留已成交量(撤前可能又成交)"
        again = exchange.query("BUYaaa111", DEFAULT_SYMBOL)
        assert again is not None and again.status is OrderStatus.CANCELED, (
            "撤单后订单仍留在簿内(有成交的撤单必须能被对账恢复)"
        )

    def test_cancel_unknown_returns_none(self) -> None:
        exchange = StatefulFakeExchange()

        assert exchange.cancel("BUYmissing", DEFAULT_SYMBOL) is None, (
            "撤不存在订单应返回 None"
        )


class TestFillScripting:
    """脚本化成交"""

    def test_partial_fill_keeps_status_open(self) -> None:
        exchange = StatefulFakeExchange()
        exchange.place(make_request())

        exchange.fill("BUYaaa111", 30.0, status=OrderStatus.OPEN)

        result = exchange.query("BUYaaa111", DEFAULT_SYMBOL)
        assert result is not None
        assert result.status is OrderStatus.OPEN, "部分成交仍是活单"
        assert result.filled_qty == 30.0, "部分成交量必须可见"

    def test_fill_unknown_order_raises(self) -> None:
        exchange = StatefulFakeExchange()

        with pytest.raises(KeyError):
            exchange.fill("BUYmissing", 100.0)


class TestFaultInjection:
    """故障注入: 精确命中 JournaledTradingPort 的两条中断路径"""

    def test_lose_response_records_order_then_raises(self) -> None:
        """响应丢失 = 请求已到达交易所, 但结果未知"""
        exchange = StatefulFakeExchange()
        exchange.lose_response_next = True

        with pytest.raises(SmartTraderError) as exc_info:
            exchange.place(make_request())

        assert exc_info.value.error_code is ErrorCode.EXCHANGE_UNAVAILABLE, (
            "响应丢失必须表现为 EXCHANGE_UNAVAILABLE(结果未知, 保留待对账)"
        )
        assert "BUYaaa111" in exchange.book, "响应丢失前订单已到达交易所, 必须在簿内"

    def test_lose_response_is_one_shot(self) -> None:
        exchange = StatefulFakeExchange()
        exchange.lose_response_next = True
        with pytest.raises(SmartTraderError):
            exchange.place(make_request("BUYfirst"))

        result = exchange.place(make_request("BUYsecond"))

        assert result.status is OrderStatus.OPEN, "注入只生效一次, 下一单应正常"

    def test_drop_request_raises_without_touching_book(self) -> None:
        """请求未到达 = 交易所从未见过该订单"""
        exchange = StatefulFakeExchange()
        exchange.drop_request_next = True

        with pytest.raises(SmartTraderError) as exc_info:
            exchange.place(make_request())

        assert exc_info.value.error_code is ErrorCode.EXCHANGE_UNAVAILABLE
        assert exchange.book == {}, "请求未到达, 订单簿必须为空(对账后删行)"


class TestPersistence:
    """JSON 持久化: 让"交易所"比崩溃的进程活得久(SIGKILL 档依赖)"""

    def test_roundtrip_across_instances(self, tmp_path: Path) -> None:
        book_path = tmp_path / "exchange_book.json"
        first = StatefulFakeExchange(persist_path=book_path)
        first.place(make_request())
        first.fill("BUYaaa111", 30.0, avg_price=0.5, status=OrderStatus.OPEN)

        second = StatefulFakeExchange(persist_path=book_path)

        assert "BUYaaa111" in second.book, "重启后的交易所实例必须读到簿内容"
        result = second.query("BUYaaa111", DEFAULT_SYMBOL)
        assert result is not None
        assert result.filled_qty == 30.0, "成交量必须跨实例持久化"
        assert result.status is OrderStatus.OPEN, "状态必须跨实例持久化"

    def test_empty_book_file_roundtrip(self, tmp_path: Path) -> None:
        book_path = tmp_path / "exchange_book.json"
        first = StatefulFakeExchange(persist_path=book_path)

        second = StatefulFakeExchange(persist_path=book_path)

        assert second.book == {}, "空簿重启后仍为空"
        assert first.book == {}
