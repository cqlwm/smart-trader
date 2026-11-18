"""策略运行态心跳文件（非账本状态）

只读控制台按账本派生策略列表, 但"刚启动、尚未产生任何订单"的策略在账本里不可见。
策略进程的 `CommandRunner` 每轮把可控策略的运行态原子写入本文件, API 读取后
与账本摘要合并, 前端即可发现并控制这类策略。

文件不是事实来源: 缺失、损坏或心跳过期都只意味着"运行态未知/离线",
不影响账本与命令执行。进程正常退出时删除文件。
"""

import os
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from smart_trader import log
from smart_trader.model import StrategyRuntime
from smart_trader.strategy.control import ControllableStrategy

logger = log.getLogger(__name__)


class RuntimeSnapshot(BaseModel):
    """一次心跳快照"""

    model_config = ConfigDict(extra="forbid")

    generated_at: str
    strategies: list[StrategyRuntime]


def _now() -> str:
    return datetime.now(UTC).isoformat()


class RuntimeRegistry:
    """运行态心跳文件的写入端（策略进程）"""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        # 命令轮询线程与配置监督线程都可能写心跳, 临时文件写入必须串行
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def heartbeat(self, strategies: Iterable[ControllableStrategy]) -> None:
        """原子写入当前运行态; 失败只记日志, 不影响策略主流程"""
        now = _now()
        snapshot = RuntimeSnapshot(
            generated_at=now,
            strategies=[
                strategy.runtime_info().model_copy(update={"heartbeat_at": now})
                for strategy in strategies
            ],
        )
        tmp = self._path.with_name(f"{self._path.name}.tmp")
        try:
            with self._lock:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(snapshot.model_dump_json(), encoding="utf-8")
                os.replace(tmp, self._path)
        except OSError as exc:
            logger.warning("运行态心跳写入失败: %s", exc)

    def read(self) -> RuntimeSnapshot | None:
        """读取快照; 缺失或损坏返回 None（只读侧不得因此报错）"""
        if not self._path.is_file():
            return None
        try:
            return RuntimeSnapshot.model_validate_json(
                self._path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            logger.warning("运行态心跳读取失败: %s", exc)
            return None

    def clear(self) -> None:
        """进程正常退出时删除心跳, 避免 UI 继续显示在线"""
        try:
            self._path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("运行态心跳清理失败: %s", exc)
