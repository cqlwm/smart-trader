from pydantic import BaseModel


class OrderResponse(BaseModel):
    order_id: str
    strategy_id: str
    symbol: str
    side: str
    position_side: str
    order_type: str
    quantity: float
    price: float | None
    status: str
    created_at: int
    updated_at: int
    filled_quantity: float
    filled_price: float
    fee: float
    stop_loss: float | None
    take_profit: float | None
