# Smart-Trader API Implementation Tasks (tasks.md)

- [ ] **Task 1: API Infrastructure Setup**
  - Create the `api` module directory structure (`api/routes`, `api/schemas`).
  - Implement `api/schemas/common.py` with `BaseResponse` model.
  - Implement `api/main.py` with the base FastAPI application instance.

- [ ] **Task 2: System Routes & Authentication**
  - Create `api/dependencies.py` and implement `verify_api_key` using `os.environ.get('API_ACCESS_KEY')`.
  - Implement `api/routes/system.py` with a simple `/health` endpoint.
  - Register the router in `api/main.py`.

- [ ] **Task 3: Refactor Trading Bot Lifecycle (`BotManager`)**
  - Extract the bot startup logic from `run.py` into a new class `BotManager` in a new file `bot_manager.py`.
  - Add `start_in_background()` method to `BotManager` using `threading.Thread`.
  - Add a `stop()` method to safely signal the `BinanceDataEventLoop` to terminate.

- [ ] **Task 4: Integrate BotManager with FastAPI Lifespan**
  - Update `api/main.py` to use a `@asynccontextmanager` for `lifespan`.
  - On startup: Instantiate `BotManager`, call `start_in_background()`, and assign it to `app.state.bot_manager`.
  - On shutdown: Call `app.state.bot_manager.stop()` and wait for the thread to join.

- [ ] **Task 5: Implement Account Endpoints**
  - Create `api/schemas/account.py` for balance and position response models.
  - Implement `api/routes/account.py` to fetch data via `app.state.bot_manager.client`.
  - Protect endpoints with `verify_api_key` dependency.

- [ ] **Task 6: Implement Strategy Endpoints**
  - Create `api/schemas/strategy.py`.
  - Implement `api/routes/strategy.py` to list active handlers from `app.state.bot_manager.event_loop.handlers`.
  - Protect endpoints with `verify_api_key` dependency.

- [ ] **Task 7: Update Entry Point**
  - Modify `run.py` so that executing `python run.py` starts the Uvicorn server (`uvicorn.run("api.main:app", host="0.0.0.0", port=8000)`).
  - Add a fallback to run without the API if a specific flag/env var is set, for backward compatibility.

- [ ] **Task 8: Unit and Integration Tests**
  - Write `tests/api/test_system.py`.
  - Write `tests/api/test_account.py` with mocked Binance client.
  - Write `tests/api/test_auth.py` to ensure `401 Unauthorized` works.
