import logging
from fastapi import APIRouter, Depends, HTTPException
from api.schemas.common import BaseResponse
from api.schemas.strategy import StrategyStatus, StrategyInfo
from api.schemas.strategy_schemas import CreateStrategyRequest, StrategyResponse, StrategyTypeInfo
from api.dependencies import verify_api_key, get_bot_manager, get_instance_manager
from strategy.instance import StrategyInstance
from strategy.instance_manager import StrategyInstanceManager
from strategy.registry import StrategyRegistry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(verify_api_key)])


def _to_response(instance: StrategyInstance) -> StrategyResponse:
    return StrategyResponse(
        instance_id=instance.instance_id,
        strategy_type=instance.strategy_type,
        status=instance.status.value,
        config=instance.config,
        created_at=instance.created_at.isoformat(),
        error_message=instance.error_message,
    )


@router.get("/strategies/status", response_model=BaseResponse[StrategyStatus])
async def get_strategies_status(bot_manager=Depends(get_bot_manager)):
    event_loop = bot_manager.data_event_loop
    is_running = event_loop is not None and bot_manager._thread is not None and bot_manager._thread.is_alive()

    strategies: list[StrategyInfo] = []
    if event_loop and hasattr(event_loop, 'handlers'):
        for handler in event_loop.handlers:
            if hasattr(handler, 'strategy'):
                strategy = handler.strategy
                strategies.append(StrategyInfo(
                    name=strategy.__class__.__name__,
                    symbols=[s.binance() for s in getattr(strategy, 'symbols', [])],
                    timeframes=getattr(strategy, 'timeframes', [])
                ))

    return BaseResponse(data=StrategyStatus(
        is_running=is_running,
        strategies=strategies
    ))


@router.post("/strategies", response_model=BaseResponse[StrategyResponse])
async def create_strategy(
    request: CreateStrategyRequest,
    mgr: StrategyInstanceManager = Depends(get_instance_manager),
):
    try:
        instance = mgr.create(request.strategy_type, request.config)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return BaseResponse(data=_to_response(instance))


@router.get("/strategies", response_model=BaseResponse[list[StrategyResponse]])
async def list_strategies(
    mgr: StrategyInstanceManager = Depends(get_instance_manager),
):
    instances = mgr.list_all()
    return BaseResponse(data=[_to_response(i) for i in instances])


@router.get("/strategies/{instance_id}", response_model=BaseResponse[StrategyResponse])
async def get_strategy(
    instance_id: str,
    mgr: StrategyInstanceManager = Depends(get_instance_manager),
):
    instance = mgr.get(instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail=f"Instance '{instance_id}' not found")
    return BaseResponse(data=_to_response(instance))


@router.post("/strategies/{instance_id}/start", response_model=BaseResponse[StrategyResponse])
async def start_strategy(
    instance_id: str,
    mgr: StrategyInstanceManager = Depends(get_instance_manager),
):
    try:
        instance = mgr.start(instance_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return BaseResponse(data=_to_response(instance))


@router.post("/strategies/{instance_id}/stop", response_model=BaseResponse[StrategyResponse])
async def stop_strategy(
    instance_id: str,
    mgr: StrategyInstanceManager = Depends(get_instance_manager),
):
    try:
        instance = mgr.stop(instance_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return BaseResponse(data=_to_response(instance))


@router.delete("/strategies/{instance_id}", response_model=BaseResponse[None])
async def delete_strategy(
    instance_id: str,
    mgr: StrategyInstanceManager = Depends(get_instance_manager),
):
    try:
        mgr.remove(instance_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return BaseResponse(data=None)


@router.get("/strategy-types", response_model=BaseResponse[list[StrategyTypeInfo]])
async def list_strategy_types():
    types = StrategyRegistry.list_types()
    return BaseResponse(data=[StrategyTypeInfo(name=t) for t in types])
