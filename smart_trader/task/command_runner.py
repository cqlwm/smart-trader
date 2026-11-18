"""命令执行器（P1）

策略进程内的单独线程: 轮询 `commands` 表 → 认领一条 → 派发到 `ControllableStrategy`
→ 写回终态。异常在此边界被捕获并记录, 轮询线程永不因单条命令失败而退出。
"""

import threading
from collections.abc import Iterable, Mapping

from smart_trader import log
from smart_trader.model import CommandAction, CommandStatus
from smart_trader.runtime import RuntimeRegistry
from smart_trader.storage.command_store import CommandStore
from smart_trader.storage.records import CommandRecord
from smart_trader.strategy.control import ControllableStrategy
from smart_trader.task.strategy_task import StrategyTask

logger = log.getLogger(__name__)


def controllable_strategies(
    tasks: Iterable[StrategyTask],
) -> dict[str, ControllableStrategy]:
    """从任务列表提取可控策略; 不可控的策略（如纯信号任务）被忽略"""
    strategies: dict[str, ControllableStrategy] = {}
    for task in tasks:
        strategy = task.strategy
        if isinstance(strategy, ControllableStrategy):
            strategies[strategy.strategy_key] = strategy
    return strategies


class CommandRunner:
    """控制命令的拉取与执行"""

    def __init__(
        self,
        store: CommandStore,
        strategies: Mapping[str, ControllableStrategy],
        poll_interval: float = 2.0,
        registry: RuntimeRegistry | None = None,
    ):
        self._store = store
        self._strategies = dict(strategies)
        self._strategies_lock = threading.Lock()
        self._poll_interval = poll_interval
        self._registry = registry
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def add_strategy(self, strategy: ControllableStrategy) -> None:
        """注册热新增的策略, 使命令与 kill switch 立即覆盖它"""
        with self._strategies_lock:
            self._strategies[strategy.strategy_key] = strategy
        self.heartbeat()
        logger.info("命令执行器新增策略: %s", strategy.strategy_key)

    def start(self) -> None:
        """启动后台轮询线程（幂等: 已启动则忽略）"""
        if self._thread is not None:
            return
        self.heartbeat()
        self._thread = threading.Thread(
            target=self._loop, name="CommandRunner", daemon=True
        )
        self._thread.start()
        logger.info("命令执行器启动: %s 个可控策略", len(self._strategies))

    def stop(self) -> None:
        """唤醒并等待轮询线程退出, 并清除运行态心跳"""
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=self._poll_interval + 1.0)
            self._thread = None
        if self._registry is not None:
            self._registry.clear()

    def heartbeat(self) -> None:
        """把当前策略运行态写入心跳文件（未配置 registry 时为空操作）"""
        if self._registry is not None:
            with self._strategies_lock:
                strategies = list(self._strategies.values())
            self._registry.heartbeat(strategies)

    def run_once(self) -> bool:
        """认领并执行一条命令; 无命令返回 False（供测试同步驱动）"""
        command = self._store.claim_next()
        if command is None:
            return False

        try:
            result = self._execute(command)
        except Exception as exc:  # 边界: 单条命令失败不得终止轮询线程
            logger.exception("命令执行失败: %s", command.id)
            self._finish(command, CommandStatus.FAILED, f"{type(exc).__name__}: {exc}")
        else:
            self._finish(command, CommandStatus.SUCCEEDED, result)
        return True

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self.heartbeat()
            if not self.run_once():
                self._stop_event.wait(self._poll_interval)
            else:
                # 命令可能改变 is_running, 执行后立即刷新心跳
                self.heartbeat()

    def _finish(
        self, command: CommandRecord, status: CommandStatus, result: str | None
    ) -> None:
        try:
            self._store.complete(command.id, status=status, result=result)
        except Exception:
            logger.exception("命令终态写入失败: %s", command.id)

    def _execute(self, command: CommandRecord) -> str:
        if command.action == CommandAction.KILL_SWITCH:
            return self._kill_switch()

        strategy_key = command.strategy_key
        if strategy_key is None:
            raise ValueError(f"{command.action.value} 缺少 strategy_key")
        with self._strategies_lock:
            strategy = self._strategies.get(strategy_key)
        if strategy is None:
            raise ValueError(f"策略未运行: {strategy_key}")

        if command.action == CommandAction.PAUSE:
            strategy.pause()
            return "已暂停策略"
        if command.action == CommandAction.RESUME:
            strategy.resume()
            return "已恢复策略"
        if command.action == CommandAction.CLOSE_ALL:
            strategy.request_close_all()
            return "全平请求已排队, 下一根完成 K 线执行"
        if command.action == CommandAction.CANCEL_ENTRIES:
            affected, archived = strategy.cancel_pending_entries()
            return f"撤销 {affected} 个仓位的入场挂单, 归档 {archived} 个空仓位"
        raise ValueError(f"不支持的命令: {command.action.value}")

    def _kill_switch(self) -> str:
        with self._strategies_lock:
            strategies = list(self._strategies.items())
        if not strategies:
            return "没有可控策略"
        failures: list[str] = []
        for strategy_key, strategy in strategies:
            try:
                strategy.request_close_all()
                strategy.pause()
            except Exception as exc:
                logger.exception("kill switch 下发失败: %s", strategy_key)
                failures.append(f"{strategy_key}: {exc}")
        if failures:
            raise RuntimeError("部分策略失败: " + "; ".join(failures))
        return f"已暂停 {len(strategies)} 个策略并提交全平请求"
