import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_LOG_LEVEL_ENV = "LOG_LEVEL"
_LOG_DIR_ENV = "LOG_DIR"
_DEFAULT_LOG_DIR = "logs"
_LOG_FILE_NAME = "smart_trader.log"
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5


def _resolve_level() -> int:
    raw = os.environ.get(_LOG_LEVEL_ENV)
    if not raw:
        return logging.INFO
    level = logging.getLevelNamesMapping().get(raw.upper())
    if level is None:
        raise ValueError(f"无效的 {_LOG_LEVEL_ENV}: {raw}")
    return level


def _resolve_log_dir() -> str:
    return os.environ.get(_LOG_DIR_ENV, _DEFAULT_LOG_DIR)


def init_logging(level: int | None = None, log_dir: str | None = None) -> None:
    """
    初始化全局日志配置, 由程序入口显式调用一次。

    禁止在模块导入期自动调用, 避免库引用或测试被动修改 root logger。

    @param level: 日志级别, 默认取环境变量 LOG_LEVEL, 未设置则为 INFO
    @param log_dir: 日志落盘目录, 默认取环境变量 LOG_DIR, 未设置则为 logs
    """
    resolved_level = level if level is not None else _resolve_level()
    resolved_log_dir = log_dir if log_dir is not None else _resolve_log_dir()
    os.makedirs(resolved_log_dir, exist_ok=True)

    formatter = logging.Formatter(_LOG_FORMAT)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(
            filename=os.path.join(resolved_log_dir, _LOG_FILE_NAME),
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        ),
    ]
    for handler in handlers:
        handler.setFormatter(formatter)

    logging.basicConfig(level=resolved_level, handlers=handlers, force=True)


def getLogger(name: str) -> logging.Logger:
    return logging.getLogger(name)
