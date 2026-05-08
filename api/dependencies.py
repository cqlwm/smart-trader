import os
from fastapi import Header, HTTPException, Request
from strategies.instance_manager import StrategyInstanceManager
import dotenv

dotenv.load_dotenv()

API_KEY = os.environ.get("API_ACCESS_KEY")


async def verify_api_key(x_api_key: str = Header(...)) -> str:
    if not API_KEY:
        raise HTTPException(status_code=500, detail="API_ACCESS_KEY not configured on server")
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return x_api_key


def get_bot_manager(request: Request):
    if not hasattr(request.app.state, "bot_manager"):
        raise HTTPException(status_code=500, detail="BotManager not initialized")
    return request.app.state.bot_manager


def get_instance_manager(request: Request) -> StrategyInstanceManager:
    if not hasattr(request.app.state, "instance_manager"):
        raise HTTPException(status_code=500, detail="StrategyInstanceManager not initialized")
    return request.app.state.instance_manager
