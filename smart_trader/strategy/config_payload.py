"""策略配置载荷: 可序列化参数与 `SignalGridStrategyConfig` 的双向转换

`SignalGridStrategyConfig.signal` 持有 `Signal` 实例, 不能直接落 JSON;
本模型用 `signal_type` + `atr_multiple` + `period` 描述信号, 加载时物化。

- `to_strategy_config(strategy_key)`: 载荷 → 策略配置（键由行提供, 载荷不含键）;
- `from_strategy_config(config, data_source)`: 模板配置 → 载荷, 仅种子脚本使用;
- `to_json` / `from_json`: 唯一的 JSON 口径（排序键, 便于 diff）。
"""

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from smart_trader.model import (
    DataSource,
    EntryMode,
    OrderSide,
    OrderSizeUnit,
    PlaceOrderBehavior,
    PositionSide,
    SignalKind,
    StrategySignalType,
    Symbol,
)
from smart_trader.strategy.alpha_trend_signal.alpha_trend_signal import AlphaTrendSignal
from smart_trader.strategy.inverted_signal import InvertedSignal
from smart_trader.strategy.signal import Signal
from smart_trader.strategy.signal_grid_config import (
    SignalGridStrategyConfig,
    validate_spacing_mode,
)


class StrategyConfigPayload(BaseModel):
    """一个策略实例的全部可序列化参数"""

    model_config = ConfigDict(extra="forbid")

    # 身份 / 数据源
    symbol: str = "DOGE/USDT"
    timeframe: str = Field(default="1m", min_length=1)
    data_source: DataSource = DataSource.TIMER

    # 信号
    signal_type: StrategySignalType = StrategySignalType.ALPHA_TREND
    atr_multiple: float = Field(default=1.0, gt=0)
    period: int = Field(default=8, gt=0)
    signal_kind: SignalKind = SignalKind.DIRECTIONAL

    # 方向
    position_side: PositionSide = PositionSide.LONG
    master_side: OrderSide = OrderSide.BUY

    # 网格 / 仓位
    per_order_amount: float = Field(default=0.02, ge=0)
    per_order_unit: OrderSizeUnit = OrderSizeUnit.BASE
    # 加仓间距口径: 必须与间距字段一致（见 validate_spacing_mode）
    entry_mode: EntryMode = EntryMode.GRID
    grid_spacing_rate: float | None = 0.01
    trend_spacing_rate: float | None = None
    max_order: int = 10000
    highest_price: float = 1000000
    lowest_price: float = 0

    # 信号止盈止损
    enable_signal_take_profit: bool = True
    enable_signal_stop_loss: bool = False
    enable_single_layer_signal_stop_loss: bool = False

    # 固定比例止盈
    fixed_rate_take_profit: bool = False
    take_profit_use_limit_order: bool = False
    fixed_take_profit_rate: float = 0.006

    # 执行
    place_order_behavior: PlaceOrderBehavior = PlaceOrderBehavior.CHASER_OPEN

    # 仓位级风控
    enable_max_order_stop_loss: bool = False
    paused_after_stop_loss: bool = True
    enable_order_stop_loss: bool = False
    order_stop_loss_rate: float = 0.05
    enable_trailing_stop: bool = False
    trailing_stop_rate: float = 0.02
    trailing_stop_activation_profit_rate: float = 0.01

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        """解析并规范化 ccxt 交易对; 空值 / 非法格式直接拒绝"""
        text = value.strip()
        if not text:
            raise ValueError("symbol 不能为空")
        return Symbol.from_ccxt(text).ccxt()

    @model_validator(mode="after")
    def _require_consistent_spacing(self) -> "StrategyConfigPayload":
        """加仓模式必须与间距字段一致"""
        validate_spacing_mode(
            self.entry_mode, self.grid_spacing_rate, self.trend_spacing_rate
        )
        return self

    def to_strategy_config(self, strategy_key: str) -> SignalGridStrategyConfig:
        """物化为策略配置; 键由配置行的主键提供"""
        if not strategy_key:
            raise ValueError("strategy_key 不能为空")
        signal: Signal | None = None
        if self.signal_type is StrategySignalType.ALPHA_TREND:
            signal = AlphaTrendSignal(
                atr_multiple=self.atr_multiple, period=self.period
            )
        elif self.signal_type is StrategySignalType.ALPHA_TREND_INVERSE:
            signal = InvertedSignal(
                AlphaTrendSignal(atr_multiple=self.atr_multiple, period=self.period)
            )
        return SignalGridStrategyConfig(
            symbol=Symbol.from_ccxt(self.symbol),
            timeframe=self.timeframe,
            position_side=self.position_side,
            master_side=self.master_side,
            per_order_amount=self.per_order_amount,
            per_order_unit=self.per_order_unit,
            entry_mode=self.entry_mode,
            grid_spacing_rate=self.grid_spacing_rate,
            trend_spacing_rate=self.trend_spacing_rate,
            max_order=self.max_order,
            highest_price=self.highest_price,
            lowest_price=self.lowest_price,
            signal=signal,
            signal_kind=self.signal_kind,
            enable_signal_take_profit=self.enable_signal_take_profit,
            enable_signal_stop_loss=self.enable_signal_stop_loss,
            enable_single_layer_signal_stop_loss=(
                self.enable_single_layer_signal_stop_loss
            ),
            fixed_rate_take_profit=self.fixed_rate_take_profit,
            take_profit_use_limit_order=self.take_profit_use_limit_order,
            fixed_take_profit_rate=self.fixed_take_profit_rate,
            place_order_behavior=self.place_order_behavior,
            strategy_key=strategy_key,
            enable_max_order_stop_loss=self.enable_max_order_stop_loss,
            paused_after_stop_loss=self.paused_after_stop_loss,
            enable_order_stop_loss=self.enable_order_stop_loss,
            order_stop_loss_rate=self.order_stop_loss_rate,
            enable_trailing_stop=self.enable_trailing_stop,
            trailing_stop_rate=self.trailing_stop_rate,
            trailing_stop_activation_profit_rate=(
                self.trailing_stop_activation_profit_rate
            ),
        )

    @classmethod
    def from_strategy_config(
        cls,
        config: SignalGridStrategyConfig,
        *,
        data_source: DataSource,
    ) -> "StrategyConfigPayload":
        """模板配置 → 载荷; 仅支持 AlphaTrendSignal / 其反向包装 / None"""
        signal = config.signal
        if isinstance(signal, InvertedSignal):
            inner = signal.inner
            if not isinstance(inner, AlphaTrendSignal):
                raise TypeError(
                    f"不支持的信号实现: InvertedSignal({type(inner).__name__}); "
                    "配置只能序列化 AlphaTrendSignal / "
                    "InvertedSignal(AlphaTrendSignal) 或 None"
                )
            signal_type = StrategySignalType.ALPHA_TREND_INVERSE
            atr_multiple = inner.atr_multiple
            period = inner.period
        elif isinstance(signal, AlphaTrendSignal):
            signal_type = StrategySignalType.ALPHA_TREND
            atr_multiple = signal.atr_multiple
            period = signal.period
        elif signal is None:
            signal_type = StrategySignalType.NONE
            atr_multiple = 1.0
            period = 8
        else:
            raise ValueError(
                f"不支持的信号实现: {type(signal).__name__}; "
                "配置只能序列化 AlphaTrendSignal / "
                "InvertedSignal(AlphaTrendSignal) 或 None"
            )
        return cls(
            symbol=config.symbol.ccxt(),
            timeframe=config.timeframe,
            data_source=data_source,
            signal_type=signal_type,
            atr_multiple=atr_multiple,
            period=period,
            signal_kind=config.signal_kind,
            position_side=config.position_side,
            master_side=config.master_side,
            per_order_amount=config.per_order_amount,
            per_order_unit=config.per_order_unit,
            entry_mode=config.entry_mode,
            grid_spacing_rate=config.grid_spacing_rate,
            trend_spacing_rate=config.trend_spacing_rate,
            max_order=config.max_order,
            highest_price=config.highest_price,
            lowest_price=config.lowest_price,
            enable_signal_take_profit=config.enable_signal_take_profit,
            enable_signal_stop_loss=config.enable_signal_stop_loss,
            enable_single_layer_signal_stop_loss=(
                config.enable_single_layer_signal_stop_loss
            ),
            fixed_rate_take_profit=config.fixed_rate_take_profit,
            take_profit_use_limit_order=config.take_profit_use_limit_order,
            fixed_take_profit_rate=config.fixed_take_profit_rate,
            place_order_behavior=config.place_order_behavior,
            enable_max_order_stop_loss=config.enable_max_order_stop_loss,
            paused_after_stop_loss=config.paused_after_stop_loss,
            enable_order_stop_loss=config.enable_order_stop_loss,
            order_stop_loss_rate=config.order_stop_loss_rate,
            enable_trailing_stop=config.enable_trailing_stop,
            trailing_stop_rate=config.trailing_stop_rate,
            trailing_stop_activation_profit_rate=(
                config.trailing_stop_activation_profit_rate
            ),
        )

    def to_json(self) -> str:
        """稳定的 JSON 文本: 排序键, 便于审计与 diff"""
        return json.dumps(
            self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
        )

    @classmethod
    def from_json(cls, text: str) -> "StrategyConfigPayload":
        """解析 JSON; 语法错误 / 非对象 / 字段不符都抛 ValueError"""
        try:
            data: Any = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"配置 JSON 解析失败: {exc}") from exc
        if not isinstance(data, dict):
            raise TypeError("配置 JSON 必须是对象")
        return cls.model_validate(data)
