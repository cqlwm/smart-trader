from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from api.routes import system, account, strategy, orders, backtest, klines
from bot_manager import BotManager
from strategies.instance_manager import StrategyInstanceManager
import logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("Starting up FastAPI application...")
    bot_mgr = BotManager()
    _app.state.bot_manager = bot_mgr
    _app.state.instance_manager = bot_mgr.instance_manager
    bot_mgr.start_in_background()
    yield
    logger.info("Shutting down FastAPI application...")
    bot_mgr.stop()

app = FastAPI(
    title="Smart-Trader API",
    description="API for Smart-Trader quantitative trading bot",
    version="1.0.0",
    lifespan=lifespan
)

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": str(exc.detail), "data": None},
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"code": 422, "message": "Validation Error", "data": exc.errors()},
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Global exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": "Internal Server Error", "data": str(exc)},
    )


# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(system.router)
app.include_router(account.router)
app.include_router(strategy.router)
app.include_router(orders.router)
app.include_router(backtest.router)
app.include_router(klines.router)
