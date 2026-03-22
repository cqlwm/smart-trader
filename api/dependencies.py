import os
from fastapi import Header, HTTPException, Request
import dotenv

dotenv.load_dotenv()

API_KEY = os.environ.get("API_ACCESS_KEY")

async def verify_api_key(x_api_key: str = Header(...)):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="API_ACCESS_KEY not configured on server")
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return x_api_key

def get_bot_manager(request: Request):
    """
    Returns the bot_manager instance from app state.
    """
    if not hasattr(request.app.state, "bot_manager"):
        raise HTTPException(status_code=500, detail="BotManager not initialized")
    return request.app.state.bot_manager
