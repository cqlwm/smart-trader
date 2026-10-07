from enum import Enum


class ErrorCode(Enum):
    """全局异常码"""

    ORDER_NOTIONAL_TOO_SMALL = "order_notional_too_small"
    ORDER_QUANTITY_TOO_SMALL = "order_quantity_too_small"
    ORDER_WOULD_TAKE_LIQUIDITY = "order_would_take_liquidity"
    ORDER_REJECTED = "order_rejected"
    ORDER_NOT_FOUND = "order_not_found"
    ORDER_NOT_PLACED = "order_not_placed"
    ORDER_RESPONSE_INVALID = "order_response_invalid"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    EXCHANGE_RATE_LIMITED = "exchange_rate_limited"
    EXCHANGE_UNAVAILABLE = "exchange_unavailable"
    EXCHANGE_ERROR = "exchange_error"
    SYMBOL_INFO_UNAVAILABLE = "symbol_info_unavailable"


class SmartTraderError(Exception):
    """带异常码的业务异常"""

    def __init__(self, error_code: ErrorCode, message: str):
        super().__init__(message)
        self.error_code: ErrorCode = error_code

    @property
    def is_transient(self) -> bool:
        """是否为瞬时错误(限频/网络): 同样参数稍后重试可能成功"""
        return self.error_code in (
            ErrorCode.EXCHANGE_RATE_LIMITED,
            ErrorCode.EXCHANGE_UNAVAILABLE,
        )
