# Smart-Trader API Specifications (spec.md)

## 1. Architecture Overview
The project currently runs a blocking `BinanceDataEventLoop` based on `websocket-client`. To expose an HTTP API without rewriting the entire core event loop to `asyncio` immediately, we will adopt a **Main Thread (FastAPI) + Background Thread (Trading Bot)** architecture.

- **FastAPI Application**: Runs on the main thread using `uvicorn`. Handles incoming HTTP requests asynchronously.
- **Trading Bot**: Runs in a dedicated background `threading.Thread`. The thread lifecycle is managed by FastAPI's `lifespan` context manager.
- **State Sharing**: The FastAPI application will maintain a reference to the `BotManager` or global `main_binance_client` inside `app.state`, allowing route handlers to read real-time data safely.

## 2. Directory Structure Updates
```text
smart-trader/
├── api/
│   ├── __init__.py
│   ├── main.py           # FastAPI app instance, CORS, lifespan, global exception handlers
│   ├── dependencies.py   # API-Key authentication and global state injection
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── system.py     # /health
│   │   ├── account.py    # /account/balances, /account/positions
│   │   └── strategy.py   # /strategies
│   └── schemas/
│       ├── __init__.py
│       ├── common.py     # BaseResponse model
│       ├── account.py    # BalanceResponse, PositionResponse
│       └── strategy.py   # StrategyListResponse
├── bot_manager.py        # Refactored from run.py to manage the background thread
```

## 3. Data Models (Schemas)
All API responses will follow a standard envelope:
```python
class BaseResponse(BaseModel, Generic[T]):
    code: int = 0
    message: str = "success"
    data: T | None = None
```

## 4. API Endpoints
### 4.1 System
- **`GET /api/v1/health`**
  - **Auth**: None
  - **Response**: `{"status": "running", "uptime_seconds": 120}`

### 4.2 Account
- **`GET /api/v1/account/balances`**
  - **Auth**: `X-API-Key` header required
  - **Response**: List of non-zero asset balances (Asset, Free, Locked).
- **`GET /api/v1/account/positions`**
  - **Auth**: `X-API-Key` header required
  - **Response**: List of current open positions (Symbol, Position Amt, Entry Price, Unrealized PnL).

### 4.3 Strategy
- **`GET /api/v1/strategies`**
  - **Auth**: `X-API-Key` header required
  - **Response**: List of running strategy handlers, symbols, and timeframes.

## 5. Security & Authentication
- A custom dependency `verify_api_key` will check for the `X-API-Key` header.
- The expected API key is loaded from the environment variable `API_ACCESS_KEY`.
- If the header is missing or invalid, a `401 Unauthorized` HTTP exception will be raised.

## 6. Testing Strategy
- Use `fastapi.testclient.TestClient` for endpoint integration testing.
- Mock `main_binance_client` to prevent actual API calls to Binance during unit tests.
- Verify `lifespan` correctly starts and stops the background thread.
