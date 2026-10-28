"""只读 API 路由（P0）

路由只做参数解析、404 映射与依赖取用; 派生与聚合全部在 `service.py`。
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles

from smart_trader import log
from smart_trader.api import command_service, config_service, service
from smart_trader.api.config_service import ConfigInUseError
from smart_trader.api.market_provider import MarketDataProvider
from smart_trader.api.market_service import build_market_chart
from smart_trader.api.schemas import (
    CommandPage,
    CommandRequest,
    CommandView,
    HealthView,
    KillSwitchRequest,
    MarketChartView,
    OrderPage,
    OverviewView,
    PositionDetailView,
    PositionView,
    StrategyConfigCreateRequest,
    StrategyConfigUpdateRequest,
    StrategyConfigView,
    StrategySummaryView,
)
from smart_trader.exceptions import SmartTraderError
from smart_trader.model import CommandAction, OrderStatus, Symbol
from smart_trader.runtime import RuntimeRegistry, RuntimeSnapshot
from smart_trader.storage.command_store import CommandStore
from smart_trader.storage.config_store import (
    ConfigKeyExistsError,
    ConfigNotFoundError,
    ConfigStore,
    ConfigVersionConflictError,
)
from smart_trader.storage.reader import OrderJournalReader
from smart_trader.strategy.config_payload import StrategyConfigPayload

logger = log.getLogger(__name__)

OrderStatusFilter = Literal["open", "closed", "canceled", "rejected", "expired", "unconfirmed"]

router = APIRouter(prefix="/api")


def _require_token(request: Request) -> None:
    """配置了 token 时校验 Authorization: Bearer"""
    token = cast(str | None, request.app.state.token)
    if token is None:
        return
    if request.headers.get("authorization") != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="unauthorized")


def _reader(request: Request) -> OrderJournalReader:
    return cast(OrderJournalReader, request.app.state.reader)


def _commands(request: Request) -> CommandStore:
    return cast(CommandStore, request.app.state.commands)


def _runtime_snapshot(request: Request) -> RuntimeSnapshot | None:
    path = cast(Path | None, request.app.state.runtime_path)
    if path is None:
        return None
    return RuntimeRegistry(path).read()


def _market_provider(request: Request) -> MarketDataProvider | None:
    return cast(
        MarketDataProvider | None, request.app.state.market_provider
    )


def _config_store(request: Request) -> ConfigStore:
    """配置仓储; 未注入时返回 503（与行情数据源同口径）"""
    store = cast(ConfigStore | None, request.app.state.configs)
    if store is None:
        raise HTTPException(status_code=503, detail="配置存储未配置")
    return store


@router.get("/health", response_model=HealthView)
def get_health(request: Request) -> HealthView:
    """服务时间 + 账本路径"""
    reader = _reader(request)
    return HealthView(
        status="ok",
        database=reader.db_path,
        server_time=datetime.now(UTC).isoformat(),
    )


@router.get("/overview", response_model=OverviewView)
def get_overview(request: Request) -> OverviewView:
    """全库聚合与告警（账本 + 运行态心跳）"""
    return service.build_overview(_reader(request), _runtime_snapshot(request))


@router.get("/strategies", response_model=list[StrategySummaryView])
def get_strategies(request: Request) -> list[StrategySummaryView]:
    """按 strategy_key 聚合的策略摘要（账本 + 运行态心跳）"""
    return service.list_strategy_summaries(
        _reader(request), _runtime_snapshot(request)
    )


@router.get("/market/chart", response_model=MarketChartView)
def get_market_chart(
    request: Request,
    symbol: str,
    timeframe: str = "1m",
    limit: Annotated[int, Query(ge=20, le=1000)] = 300,
) -> MarketChartView:
    """K 线 + AlphaTrend 指标（公共行情, 不触达交易）"""
    provider = _market_provider(request)
    if provider is None:
        raise HTTPException(status_code=503, detail="行情数据源未配置")
    try:
        market = provider.get()
    except Exception as exc:  # 边界: 行情初始化失败不得拖垮控制台
        logger.exception("行情数据源初始化失败")
        raise HTTPException(status_code=503, detail="行情数据源不可用") from exc

    try:
        return build_market_chart(
            market, Symbol.from_ccxt(symbol), timeframe, limit
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SmartTraderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/configs/defaults", response_model=StrategyConfigPayload)
def get_config_defaults(request: Request) -> StrategyConfigPayload:
    """新建配置表单的默认值（必须先于 /configs/{strategy_key} 声明）"""
    _config_store(request)
    return StrategyConfigPayload()


@router.get("/configs", response_model=list[StrategyConfigView])
def get_configs(request: Request) -> list[StrategyConfigView]:
    """全部策略配置（按策略键排序）"""
    return config_service.list_config_views(_config_store(request))


@router.get("/configs/{strategy_key}", response_model=StrategyConfigView)
def get_config(request: Request, strategy_key: str) -> StrategyConfigView:
    view = config_service.get_config_view(_config_store(request), strategy_key)
    if view is None:
        raise HTTPException(status_code=404, detail="config not found")
    return view


@router.post("/configs", response_model=StrategyConfigView, status_code=201)
def create_config(
    request: Request, body: StrategyConfigCreateRequest
) -> StrategyConfigView:
    """新建配置; 策略键冲突返回 409"""
    try:
        return config_service.create_config(
            _config_store(request),
            strategy_key=body.strategy_key,
            enabled=body.enabled,
            payload=body.payload,
        )
    except ConfigKeyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/configs/{strategy_key}", response_model=StrategyConfigView)
def update_config(
    request: Request, strategy_key: str, body: StrategyConfigUpdateRequest
) -> StrategyConfigView:
    """覆盖更新; 版本冲突或活跃仓位护栏返回 409"""
    try:
        return config_service.update_config(
            _config_store(request),
            _reader(request),
            strategy_key,
            enabled=body.enabled,
            expected_version=body.expected_version,
            payload=body.payload,
        )
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ConfigVersionConflictError, ConfigInUseError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/positions", response_model=list[PositionView])
def get_positions(
    request: Request,
    strategy_key: str | None = None,
    include_closed: bool = False,
) -> list[PositionView]:
    """逻辑仓位派生视图"""
    return service.list_position_views(
        _reader(request),
        strategy_key=strategy_key,
        include_closed=include_closed,
    )


@router.get("/positions/{position_id}", response_model=PositionDetailView)
def get_position_detail(request: Request, position_id: str) -> PositionDetailView:
    """仓位详情 + 全部订单"""
    detail = service.get_position_detail(_reader(request), position_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="position not found")
    return detail


@router.get("/orders", response_model=OrderPage)
def get_orders(
    request: Request,
    strategy_key: str | None = None,
    status: OrderStatusFilter | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OrderPage:
    """订单分页; status=unconfirmed 映射 status IS NULL"""
    unconfirmed = status == "unconfirmed"
    parsed = (
        None
        if status is None or status == "unconfirmed"
        else OrderStatus(status)
    )
    return service.list_order_views(
        _reader(request),
        strategy_key=strategy_key,
        status=parsed,
        unconfirmed=unconfirmed,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/strategies/{strategy_key}/commands",
    response_model=CommandView,
    status_code=202,
)
def submit_strategy_command(
    request: Request, strategy_key: str, body: CommandRequest
) -> CommandView:
    """提交策略级控制命令（pause / resume / close_all / cancel_entries）"""
    if body.action is CommandAction.KILL_SWITCH:
        raise HTTPException(
            status_code=400,
            detail="kill_switch 请使用 POST /api/commands/kill-switch",
        )
    try:
        record = _commands(request).submit(
            action=body.action,
            strategy_key=strategy_key,
            idempotency_key=body.idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return command_service.command_view(record)


@router.post(
    "/commands/kill-switch", response_model=CommandView, status_code=202
)
def submit_kill_switch(request: Request, body: KillSwitchRequest) -> CommandView:
    """全局 kill switch: 所有可控策略先全平再暂停"""
    record = _commands(request).submit(
        action=CommandAction.KILL_SWITCH,
        strategy_key=None,
        idempotency_key=body.idempotency_key,
    )
    return command_service.command_view(record)


@router.get("/commands", response_model=CommandPage)
def get_commands(
    request: Request,
    strategy_key: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CommandPage:
    """命令审计（按提交时间倒序）"""
    return command_service.list_command_views(
        _commands(request),
        strategy_key=strategy_key,
        limit=limit,
        offset=offset,
    )


@router.get("/commands/{command_id}", response_model=CommandView)
def get_command(request: Request, command_id: str) -> CommandView:
    view = command_service.get_command_view(_commands(request), command_id)
    if view is None:
        raise HTTPException(status_code=404, detail="command not found")
    return view


def create_app(
    reader: OrderJournalReader,
    command_store: CommandStore,
    *,
    token: str | None = None,
    static_dir: Path | None = None,
    runtime_path: Path | None = None,
    market_provider: MarketDataProvider | None = None,
    config_store: ConfigStore | None = None,
) -> FastAPI:
    """装配控制台 API; 依赖注入 app.state 便于测试"""
    app = FastAPI(title="Smart Trader Console API", version="0.1.0")
    app.state.reader = reader
    app.state.commands = command_store
    app.state.token = token
    app.state.runtime_path = runtime_path
    app.state.market_provider = market_provider
    app.state.configs = config_store
    app.include_router(
        router,
        dependencies=[Depends(_require_token)],
    )
    if static_dir is not None:
        index = static_dir / "index.html"
        if not index.is_file():
            raise ValueError(f"静态目录缺少 index.html: {static_dir}")
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="web")
    return app
