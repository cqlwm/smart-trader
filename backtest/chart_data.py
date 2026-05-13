from __future__ import annotations

from typing import Any, Protocol


class ChartDataProvider(Protocol):
    def get_chart_data(self) -> dict[str, list[dict[str, Any]]]:
        """返回 {category: [items]} 格式的覆盖层数据"""
        ...


def extract_trade_markers(trade_history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从交易历史提取入场/出场标记"""
    markers: list[dict[str, Any]] = []

    for order in trade_history:
        if order.get("status") != "closed":
            continue
        if order.get("filled_quantity", 0) <= 0:
            continue

        order_id = order.get("id", "")
        position_side = order.get("position_side", "long")
        side = order.get("side", "buy")
        filled_price = order.get("filled_price", 0)
        timestamp = order.get("timestamp", 0)

        is_entry = (
            (position_side == "long" and side == "buy")
            or (position_side == "short" and side == "sell")
        )
        is_exit = order_id.startswith("exit_")

        if is_exit:
            entry_id = order_id[len("exit_"):]
            markers.append({
                "type": "EXIT",
                "side": position_side.upper(),
                "price": filled_price,
                "time": timestamp,
                "order_id": entry_id,
            })
        elif is_entry:
            markers.append({
                "type": "ENTRY",
                "side": position_side.upper(),
                "price": filled_price,
                "time": timestamp,
                "order_id": order_id,
            })

    markers.sort(key=lambda m: m["time"])
    return markers
