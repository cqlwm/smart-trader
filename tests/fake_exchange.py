"""状态化内存交易所: 集成测试的"交易所事实"来源

与回放式 `tests.fakes.FakeTradingPort` 的区别:

- place 记账入簿, query/cancel 读簿 —— 存在真实的交易所状态;
- 跨策略重建(模拟重启)存活: 进程死了, 交易所还活着;
- 故障注入精确命中 `JournaledTradingPort` 的两条中断路径:
  `lose_response_next`(请求已到达但响应丢失 → 账本保留 status 为空的行待对账)
  与 `drop_request_next`(请求未到达交易所 → 对账后删行);
- 可选 JSON 持久化: SIGKILL 子进程档用它让"交易所"比崩溃的进程活得久。

仅面向测试: 不做撮合, 成交一律由用例通过 `fill()` 按脚本注入。
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from smart_trader.exceptions import ErrorCode, SmartTraderError
from smart_trader.exchange.ports import AccountPort, TradingPort
from smart_trader.model import (
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    Position,
    PositionSide,
    Symbol,
)
from tests.fakes import DEFAULT_SYMBOL


@dataclass
class BookEntry:
    """订单簿中的一笔委托: 交易所视角的客观事实

    `observed` 表示该笔订单的当前簿内状态是否已被策略查询过:
    策略对成交是惰性感知(只在离场决策/盘中路径/出场跟踪时 query),
    账本保存的是"最近一次查询时的事实", 因此对账断言只对已观察的
    订单比对执行事实(status/filled/avg_price)。
    """

    symbol: str
    side: OrderSide
    quantity: float
    price: float | None
    filled_qty: float = 0.0
    avg_price: float | None = None
    status: OrderStatus = OrderStatus.OPEN
    observed: bool = False


def _dump_entry(entry: BookEntry) -> dict[str, object]:
    data = asdict(entry)
    data["side"] = entry.side.value
    data["status"] = entry.status.value
    del data["observed"]  # 观察标记是会话内状态, 不持久化(重载后视为未观察)
    return data


def _load_entry(data: dict[str, object]) -> BookEntry:
    return BookEntry(
        symbol=str(data["symbol"]),
        side=OrderSide(str(data["side"])),
        quantity=float(data["quantity"]),  # type: ignore[arg-type]
        price=data["price"],  # type: ignore[arg-type]
        filled_qty=float(data["filled_qty"]),  # type: ignore[arg-type]
        avg_price=data["avg_price"],  # type: ignore[arg-type]
        status=OrderStatus(str(data["status"])),
    )


class StatefulFakeExchange(TradingPort, AccountPort):
    """内存订单簿交易所, 支持 one-shot 故障注入与 JSON 持久化

    AccountPort 语义: 净持仓 = 簿内 BUY 成交 - SELL 成交 - 外部平仓脚本扣减,
    与真实交易所"订单成交产生持仓"的因果关系一致。
    """

    def __init__(self, persist_path: str | Path | None = None) -> None:
        self.book: dict[str, BookEntry] = {}
        self.lose_response_next = False
        self.drop_request_next = False
        # 外部手动平仓累计扣减(不经订单簿, 模拟用户直接在交易所操作)
        self.external_reduction = 0.0
        self._persist_path = Path(persist_path) if persist_path is not None else None
        if self._persist_path is not None and self._persist_path.exists():
            self._load()

    # ---- TradingPort ----

    def place(self, request: OrderRequest) -> OrderResult:
        if self.drop_request_next:
            self.drop_request_next = False
            raise SmartTraderError(ErrorCode.EXCHANGE_UNAVAILABLE, "请求未到达交易所")
        if self.lose_response_next:
            self.lose_response_next = False
            self._record(request)
            raise SmartTraderError(ErrorCode.EXCHANGE_UNAVAILABLE, "响应丢失")
        self._record(request)
        self.book[request.custom_id].observed = True  # place 返回值策略已收到
        return self._result(request.custom_id)

    def query(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
        entry = self.book.get(custom_id)
        if entry is None:
            return None
        entry.observed = True
        return self._result(custom_id)

    def cancel(self, custom_id: str, symbol: Symbol) -> OrderResult | None:
        entry = self.book.get(custom_id)
        if entry is None:
            return None
        entry.status = OrderStatus.CANCELED
        entry.observed = True
        self._persist()
        return self._result(custom_id)

    # ---- 成交脚本 ----

    def fill(
        self,
        custom_id: str,
        filled_qty: float,
        *,
        avg_price: float | None = None,
        status: OrderStatus = OrderStatus.CLOSED,
    ) -> None:
        """脚本化成交: 模拟行情走到委托价后的成交回报

        @param status 部分成交传 OPEN, 全部成交用默认 CLOSED
        """
        entry = self.book[custom_id]
        entry.filled_qty = filled_qty
        entry.avg_price = avg_price if avg_price is not None else entry.price
        entry.status = status
        entry.observed = False  # 新事实尚未被策略查询
        self._persist()

    # ---- AccountPort ----

    def balance(self, coin: str) -> float:
        return 100.0

    def positions(self, symbol: Symbol | None = None) -> list[Position]:
        """净持仓 = BUY 成交 - SELL 成交 - 外部平仓扣减(多头为正, 空头为负)"""
        net = self.net_position()
        if net == 0:
            return []
        target = symbol if symbol is not None else DEFAULT_SYMBOL
        return [
            Position(
                symbol=target,
                side=PositionSide.LONG if net > 0 else PositionSide.SHORT,
                quantity=abs(net),
                entry_price=0.5,
            )
        ]

    def net_position(self) -> float:
        """当前净持仓量(多头为正; 含外部平仓扣减)"""
        bought = sum(
            entry.filled_qty
            for entry in self.book.values()
            if entry.side is OrderSide.BUY
        )
        sold = sum(
            entry.filled_qty
            for entry in self.book.values()
            if entry.side is OrderSide.SELL
        )
        return bought - sold - self.external_reduction

    def external_close(self, qty: float) -> None:
        """模拟外部手动平仓: 直接扣减净持仓, 不经订单簿"""
        self.external_reduction += qty
        self._persist()

    # ---- 内部 ----

    def _record(self, request: OrderRequest) -> None:
        self.book[request.custom_id] = BookEntry(
            symbol=request.symbol.ccxt(),
            side=request.side,
            quantity=request.quantity,
            price=request.price,
        )
        self._persist()

    def _result(self, custom_id: str) -> OrderResult:
        entry = self.book[custom_id]
        return OrderResult(
            custom_id=custom_id,
            symbol=Symbol.from_ccxt(entry.symbol),
            side=entry.side,
            status=entry.status,
            quantity=entry.quantity,
            filled_qty=entry.filled_qty,
            price=entry.price,
            avg_price=entry.avg_price,
        )

    def _persist(self) -> None:
        if self._persist_path is None:
            return
        data = {key: _dump_entry(entry) for key, entry in self.book.items()}
        self._persist_path.write_text(json.dumps(data, ensure_ascii=False))

    def _load(self) -> None:
        assert self._persist_path is not None
        raw = json.loads(self._persist_path.read_text())
        self.book = {key: _load_entry(value) for key, value in raw.items()}
