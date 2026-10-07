import os

import dotenv

from smart_trader import log

dotenv.load_dotenv()

logger = log.getLogger(__name__)

PROJECT_PATH = os.environ.get("PROJECT_PATH")
if not PROJECT_PATH:
    raise ValueError("PROJECT_PATH must be set")

DATA_PATH = f"{PROJECT_PATH}/.data"
# 订单账本（sqlite）: 多策略共享, 以 strategy_key 归属
DB_PATH = f"{DATA_PATH}/smart_trader.db"

os.makedirs(DATA_PATH, exist_ok=True)

logger.info("PROJECT_PATH: %s", PROJECT_PATH)
logger.info("DATA_PATH: %s", DATA_PATH)
