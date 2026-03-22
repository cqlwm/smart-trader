import time
from fastapi import APIRouter
from api.schemas.common import BaseResponse

router = APIRouter(prefix="/api/v1")

START_TIME = time.time()

@router.get("/health", response_model=BaseResponse[dict[str, str | float]])
async def health_check():
    uptime = time.time() - START_TIME
    return BaseResponse(
        data={
            "status": "running",
            "uptime_seconds": round(uptime, 2)
        }
    )
