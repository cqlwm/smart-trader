"""以表格形式输出 .data 目录下所有 JSON 文件 orders 字段的内容

运行:
    uv run python demos/data_orders_table_demo.py
    uv run python demos/data_orders_table_demo.py --data-dir .data

输出:
    每个 JSON 文件一个表格, 表标题为文件名, 表格列为 orders 每项中的
    entry_id / price / quantity / signal_min_take_profit_rate / stop_loss_rate
"""

import argparse
import json
from pathlib import Path
from typing import Any, cast

DEFAULT_DATA_DIR = Path(".data")

COLUMNS: tuple[str, ...] = (
    "entry_id",
    "price",
    "quantity",
    "signal_min_take_profit_rate",
    "stop_loss_rate",
)

# 数值列右对齐, 便于比较大小; entry_id 保持左对齐
RIGHT_ALIGNED_COLUMNS: frozenset[str] = frozenset(COLUMNS[1:])


def load_orders(json_path: Path) -> list[dict[str, Any]] | None:
    """读取 JSON 文件并返回其 orders 列表, 结构不符时返回 None"""
    raw: object = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return None
    payload = cast(dict[str, Any], raw)
    orders = payload.get("orders")
    if not isinstance(orders, list):
        return None
    return [cast(dict[str, Any], order) for order in cast(list[Any], orders)]


def _format_cell(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _render_row(cells: list[str], widths: list[int], header: bool) -> str:
    parts: list[str] = []
    for column, cell, width in zip(COLUMNS, cells, widths, strict=True):
        if not header and column in RIGHT_ALIGNED_COLUMNS:
            parts.append(cell.rjust(width))
        else:
            parts.append(cell.ljust(width))
    return "| " + " | ".join(parts) + " |"


def _render_separator(widths: list[int]) -> str:
    return "+" + "+".join("-" * (width + 2) for width in widths) + "+"


def render_table(title: str, rows: list[dict[str, Any]]) -> str:
    """把一批 order 渲染成带标题的文本表格"""
    header = list(COLUMNS)
    body = [[_format_cell(row.get(column)) for column in COLUMNS] for row in rows]

    widths = [len(column) for column in header]
    for row in body:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    separator = _render_separator(widths)
    lines = [title, separator, _render_row(header, widths, header=True), separator]
    for row in body:
        lines.append(_render_row(row, widths, header=False))
    lines.append(separator)
    if not body:
        lines.append("(orders 为空)")
    return "\n".join(lines)


def render_data_dir(data_dir: Path) -> str:
    """渲染目录下所有 JSON 文件的表格, 文件之间空行分隔"""
    json_files = sorted(data_dir.rglob("*.json"))
    if not json_files:
        return f"未在 {data_dir} 中找到 JSON 文件"

    sections: list[str] = []
    for json_path in json_files:
        title = str(json_path.relative_to(data_dir))
        try:
            orders = load_orders(json_path)
        except (OSError, json.JSONDecodeError) as exc:
            sections.append(f"{title}\n读取失败: {exc}")
            continue
        if orders is None:
            sections.append(f"{title}\n(缺少 orders 字段)")
            continue
        sections.append(render_table(title, orders))
    return "\n\n".join(sections)


def main() -> None:
    parser = argparse.ArgumentParser(description="输出 .data 下 JSON 文件 orders 的表格")
    parser.add_argument(
        "--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="数据目录, 默认 .data"
    )
    args = parser.parse_args()

    print(render_data_dir(args.data_dir))


if __name__ == "__main__":
    main()
