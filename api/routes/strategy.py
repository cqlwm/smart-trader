from fastapi import APIRouter, Depends
from api.schemas.common import BaseResponse
from api.schemas.strategy import StrategyStatus, StrategyInfo
from api.dependencies import verify_api_key, get_bot_manager

router = APIRouter(prefix="/api/v1", dependencies=[Depends(verify_api_key)])

@router.get("/strategies", response_model=BaseResponse[StrategyStatus])
async def get_strategies(bot_manager=Depends(get_bot_manager)):
    event_loop = bot_manager.data_event_loop
    is_running = event_loop is not None and bot_manager._thread is not None and bot_manager._thread.is_alive()
    
    strategies = []
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
