"""按策略 Key 分组展示账本中的有效逻辑仓位与总体盈亏

从订单账本(sqlite)读取 active=1 的逻辑仓位, 按 strategy_key 分组渲染文本表格;
价格 / 数量不是存储字段, 由仓位关联订单实时派生(见 GridPosition.price / .quantity)。

每个策略 Key 额外输出已实现盈亏: 以全部仓位（含已归档）的成交订单按平均成本法
结算, 见 GridPosition.realized_pnl; 未平仓部分的浮动盈亏需要最新行情, 不在本报表内。

运行:
    uv run python demos/positions_overview_demo.py
    uv run python demos/positions_overview_demo.py --db /path/to/smart_trader.db
"""

import argparse
import sqlite3
import unicodedata
from pathlib import Path

from smart_trader import log
from smart_trader.storage.order_journal import OrderJournal
from smart_trader.strategy.grid_position import GridPosition, load_all_positions

log.init_logging()
logger = log.getLogger(__name__)

DEFAULT_DB_PATH = ".data/smart_trader.db"
HEADERS = ("id", "价格", "数量")


def load_strategy_keys(db_path: str) -> list[str]:
    """查询账本中出现过的策略 Key（含已归档仓位）, 按字母序返回"""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT strategy_key FROM grid_positions ORDER BY strategy_key"
        ).fetchall()
    return [str(row[0]) for row in rows]


def _fmt(value: float) -> str:
    """数字转字符串, 去掉多余尾零(如 0.0020 -> 0.002)"""
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text or "0"


def _display_width(text: str) -> int:
    """终端显示宽度: 全角/宽字符(如中文表头)按 2 列计"""
    return sum(
        2 if unicodedata.east_asian_width(char) in ("F", "W") else 1
        for char in text
    )


def _pad(text: str, width: int) -> str:
    return text + " " * (width - _display_width(text))


def render_table(title: str, rows: list[tuple[str, str, str]]) -> str:
    """渲染文本表格: 标题行 + 表头 + 分隔线 + 数据行, 列宽取最宽单元格"""
    cells = [HEADERS, *rows]
    widths = [
        max(_display_width(row[column]) for row in cells) for column in range(3)
    ]
    lines = [f"## {title}", ""]
    for index, row in enumerate(cells):
        padded = (
            _pad(cell, widths[column]) for column, cell in enumerate(row)
        )
        lines.append("  ".join(padded).rstrip())
        if index == 0:
            lines.append("  ".join("-" * width for width in widths))
    return "\n".join(lines)


def report(journal: OrderJournal, db_path: str) -> None:
    """按策略 Key 分组打印有效仓位表格与已实现盈亏（盈亏含已归档仓位）"""
    keys = load_strategy_keys(db_path)
    if not keys:
        logger.info("账本 %s 中没有逻辑仓位", db_path)
        return
    total_positions = 0
    total_pnl = 0.0
    for key in keys:
        positions: list[GridPosition] = load_all_positions(journal, key)
        active = [position for position in positions if position.active]
        pnl = sum(position.realized_pnl for position in positions)
        total_positions += len(active)
        total_pnl += pnl
        if active:
            blocks = [
                render_table(
                    key,
                    [
                        (position.id, _fmt(position.price), _fmt(position.quantity))
                        for position in active
                    ],
                )
            ]
        else:
            blocks = [f"## {key}", "无有效逻辑仓位"]
        blocks.append(f"已实现盈亏: {_fmt(pnl)}")
        logger.info("\n%s", "\n\n".join(blocks))
    logger.info(
        "共 %s 个策略 Key, %s 个有效逻辑仓位, 已实现盈亏合计 %s",
        len(keys),
        total_positions,
        _fmt(total_pnl),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="按策略 Key 分组展示有效逻辑仓位与已实现盈亏"
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"订单账本 sqlite 路径, 默认 {DEFAULT_DB_PATH}",
    )
    args = parser.parse_args()

    if not Path(args.db).is_file():
        logger.error("账本文件不存在: %s", args.db)
        raise SystemExit(1)

    # 打开即校验 schema, 结构过时会响亮报错并指向 scripts/migrations/
    journal = OrderJournal(args.db)
    try:
        report(journal, args.db)
    finally:
        journal.close()


if __name__ == "__main__":
    main()
