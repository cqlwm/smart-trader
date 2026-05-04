import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import verify_api_key, get_bot_manager
from api.schemas.common import BaseResponse
from api.schemas.order import OrderResponse
from model import Order, OrderStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(verify_api_key)])


def _to_response(order: Order) -> OrderResponse:
    return OrderResponse(
        order_id=order.order_id,
        strategy_id=order.strategy_id,
        symbol=order.symbol.ccxt(),
        side=order.side.value,
        position_side=order.position_side.value,
        order_type=order.order_type,
        quantity=order.quantity,
        price=order.price,
        status=order.status.value,
        created_at=order.created_at,
        updated_at=order.updated_at,
        filled_quantity=order.filled_quantity,
        filled_price=order.filled_price,
        fee=order.fee,
        stop_loss=order.stop_loss,
        take_profit=order.take_profit,
    )


@router.get("/orders", response_model=BaseResponse[list[OrderResponse]])
async def list_orders(
    status: str | None = Query(None, description="Filter by status: open, closed, canceled"),
    strategy_id: str | None = Query(None),
    symbol: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    bot_manager=Depends(get_bot_manager),
):
    client = bot_manager.main_binance_client
    if client is None:
        raise HTTPException(status_code=503, detail="Binance client not initialized")

    order_repo = client.order_repo

    if status == "open":
        orders = order_repo.find_open_orders(strategy_id=strategy_id)
    elif status == "closed":
        orders = order_repo.find_history(strategy_id=strategy_id)
    elif status == "canceled":
        all_orders = order_repo.find_active_orders(strategy_id=strategy_id)
        orders = [o for o in all_orders if OrderStatus.is_canceled(o.status)]
    else:
        orders = order_repo.find_active_orders(strategy_id=strategy_id)

    if symbol is not None:
        orders = [o for o in orders if o.symbol.ccxt() == symbol or o.symbol.binance() == symbol]

    total = len(orders)
    paginated = orders[offset:offset + limit]

    return BaseResponse(
        data=[_to_response(o) for o in paginated],
        meta={"total": total, "limit": limit, "offset": offset},
    )


@router.get("/orders/{order_id}", response_model=BaseResponse[OrderResponse])
async def get_order(
    order_id: str,
    bot_manager=Depends(get_bot_manager),
):
    client = bot_manager.main_binance_client
    if client is None:
        raise HTTPException(status_code=503, detail="Binance client not initialized")

    order = client.order_repo.find_by_id(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order '{order_id}' not found")
    return BaseResponse(data=_to_response(order))


@router.get("/strategies/{instance_id}/orders", response_model=BaseResponse[list[OrderResponse]])
async def list_strategy_orders(
    instance_id: str,
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    bot_manager=Depends(get_bot_manager),
):
    client = bot_manager.main_binance_client
    if client is None:
        raise HTTPException(status_code=503, detail="Binance client not initialized")

    order_repo = client.order_repo
    strategy_id = instance_id

    if status == "open":
        orders = order_repo.find_open_orders(strategy_id=strategy_id)
    elif status == "closed":
        orders = order_repo.find_history(strategy_id=strategy_id)
    else:
        orders = order_repo.find_active_orders(strategy_id=strategy_id)

    total = len(orders)
    paginated = orders[offset:offset + limit]

    return BaseResponse(
        data=[_to_response(o) for o in paginated],
        meta={"total": total, "limit": limit, "offset": offset},
    )
