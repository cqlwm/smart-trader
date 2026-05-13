import logging

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import verify_api_key
from api.schemas.common import BaseResponse
from api.schemas.klines import KlineDataResponse, KlinePointResponse
from model import Symbol
from persistence.kline_data_store import KlineDataStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/klines", dependencies=[Depends(verify_api_key)])


def _parse_symbol(symbol_str: str) -> Symbol:
    parts = symbol_str.replace(":USDT", "").split("/")
    if len(parts) != 2:
        raise ValueError(f"Invalid symbol format: {symbol_str}. Expected 'BASE/QUOTE' or 'BASE/QUOTE:USDT'")
    return Symbol(base=parts[0], quote=parts[1])


@router.get("", response_model=BaseResponse[KlineDataResponse])
async def get_klines(
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
):
    try:
        parsed_symbol = _parse_symbol(symbol)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        data_store = KlineDataStore()
        file_path = data_store.ensure_data(parsed_symbol, timeframe, start_date, end_date)
        klines = data_store.load_csv(file_path, parsed_symbol, timeframe)

        from datetime import datetime, timezone
        start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())

        filtered = [
            k for k in klines
            if start_ts <= k.timestamp / 1000 < end_ts
        ]

        points = [
            KlinePointResponse(
                time=k.timestamp // 1000,
                open=k.open,
                high=k.high,
                low=k.low,
                close=k.close,
                volume=k.volume,
            )
            for k in filtered
        ]

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Kline data not found for the given parameters")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to fetch klines: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch kline data: {str(e)}")

    return BaseResponse(data=KlineDataResponse(
        symbol=symbol,
        timeframe=timeframe,
        klines=points,
    ))
