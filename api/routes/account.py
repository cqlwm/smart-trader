from typing import List
from fastapi import APIRouter, Depends
from api.schemas.common import BaseResponse
from api.schemas.account import Balance, Position
from api.dependencies import verify_api_key, get_bot_manager

router = APIRouter(prefix="/api/v1/account", dependencies=[Depends(verify_api_key)])

@router.get("/balances", response_model=BaseResponse[List[Balance]])
async def get_balances(bot_manager=Depends(get_bot_manager)):
    client = bot_manager.main_binance_client
    # ccxt fetch_balance
    raw_balances = client.exchange.fetch_balance()
    balances = []
    
    # fetch_balance returns a dict where 'info' has the raw data, and the rest are parsed.
    # Usually we can just iterate over the parsed items.
    for asset, data in raw_balances.items():
        if asset in ['info', 'free', 'used', 'total', 'timestamp', 'datetime']:
            continue
        # Filter out dust or zero balances if needed
        free = float(data.get('free', 0))
        used = float(data.get('used', 0))
        if free > 0 or used > 0:
            balances.append(Balance(
                asset=asset,
                free=free,
                locked=used
            ))
            
    return BaseResponse(data=balances)

@router.get("/positions", response_model=BaseResponse[List[Position]])
async def get_positions(bot_manager=Depends(get_bot_manager)):
    client = bot_manager.main_binance_client
    raw_positions = client.positions()
    positions = []
    
    for pos in raw_positions:
        # pos is parsed by ccxt, containing 'symbol', 'contracts', 'entryPrice', 'unrealizedPnl', 'leverage', etc.
        contracts = float(pos.get('contracts', 0))
        if contracts > 0:
            positions.append(Position(
                symbol=pos.get('symbol', ''),
                positionAmt=contracts if pos.get('side') == 'long' else -contracts,
                entryPrice=float(pos.get('entryPrice', 0)),
                unRealizedProfit=float(pos.get('unrealizedPnl', 0) or 0),
                leverage=int(pos.get('leverage', 1) or 1)
            ))
            
    return BaseResponse(data=positions)
