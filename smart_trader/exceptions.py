from enum import Enum


class ErrorCode(Enum):
    """全局异常码"""

    ORDER_NOTIONAL_TOO_SMALL = "order_notional_too_small"


class SmartTraderError(Exception):
    """带异常码的业务异常"""

    def __init__(self, error_code: ErrorCode, message: str):
        super().__init__(message)
        self.error_code: ErrorCode = error_code