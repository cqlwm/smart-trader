import logging
import sys

log_level = logging.INFO


def build_logger(name=None):
    # 创建日志记录器
    logger = logging.getLogger(name)
    logger.setLevel(log_level)  # 设置日志级别

    # 创建一个处理器，将日志输出到标准输出
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)

    # 创建一个格式化器，并将其添加到处理器中
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)

    # 将处理器添加到日志记录器中
    logger.addHandler(handler)
    return logger
