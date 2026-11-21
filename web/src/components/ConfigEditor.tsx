import type { ReactNode } from "react";

import type {
  DataSource,
  EntryMode,
  OrderSide,
  OrderSizeUnit,
  PlaceOrderBehavior,
  PositionSide,
  SignalKind,
  StrategyConfigPayload,
  StrategySignalType,
} from "../api/types";

export interface Draft {
  strategy_key: string;
  enabled: boolean;
  symbol: string;
  timeframe: string;
  data_source: DataSource;
  signal_type: StrategySignalType;
  atr_multiple: string;
  period: string;
  signal_kind: SignalKind;
  position_side: PositionSide;
  master_side: OrderSide;
  per_order_amount: string;
  per_order_unit: OrderSizeUnit;
  entry_mode: EntryMode;
  grid_spacing_rate: string;
  trend_spacing_rate: string;
  max_order: string;
  highest_price: string;
  lowest_price: string;
  enable_signal_take_profit: boolean;
  enable_signal_stop_loss: boolean;
  enable_single_layer_signal_stop_loss: boolean;
  fixed_rate_take_profit: boolean;
  take_profit_use_limit_order: boolean;
  fixed_take_profit_rate: string;
  place_order_behavior: PlaceOrderBehavior;
  enable_max_order_stop_loss: boolean;
  paused_after_stop_loss: boolean;
  enable_order_stop_loss: boolean;
  order_stop_loss_rate: string;
  enable_trailing_stop: boolean;
  trailing_stop_rate: string;
  trailing_stop_activation_profit_rate: string;
}

export interface EditorState {
  mode: "create" | "edit";
  version: number;
  draft: Draft;
}

const DATA_SOURCE_OPTIONS: { value: DataSource; label: string }[] = [
  { value: "ws", label: "ws · WebSocket 推送" },
  { value: "timer", label: "timer · REST 定时" },
];

const SIGNAL_TYPE_OPTIONS: { value: StrategySignalType; label: string }[] = [
  { value: "alpha_trend", label: "alpha_trend" },
  { value: "alpha_trend_inverse", label: "alpha_trend_inverse · 反向解读" },
  { value: "none", label: "none（无信号）" },
];

const SIGNAL_KIND_OPTIONS: { value: SignalKind; label: string }[] = [
  { value: "directional", label: "directional · 有方向倾向" },
  { value: "trigger", label: "trigger · 无方向倾向" },
];

const POSITION_SIDE_OPTIONS: { value: PositionSide; label: string }[] = [
  { value: "long", label: "long · 多头" },
  { value: "short", label: "short · 空头" },
];

const ORDER_SIZE_UNIT_OPTIONS: { value: OrderSizeUnit; label: string }[] = [
  { value: "base", label: "币数量" },
  { value: "quote", label: "USDT 名义价值" },
];

const ENTRY_MODE_OPTIONS: { value: EntryMode; label: string }[] = [
  { value: "grid", label: "grid · 网格间距（逆势补仓）" },
  { value: "trend", label: "trend · 顺势间距（顺势阶梯）" },
];

type SignalStopLossMode = "off" | "two_layers" | "single_layer";

const SIGNAL_STOP_LOSS_MODE_OPTIONS: {
  value: SignalStopLossMode;
  label: string;
}[] = [
  { value: "off", label: "关闭 · 不按信号止损" },
  { value: "two_layers", label: "两层浮亏触发" },
  { value: "single_layer", label: "一层浮亏触发（更敏感）" },
];

const MASTER_SIDE_OPTIONS: { value: OrderSide; label: string }[] = [
  { value: "buy", label: "buy · 买" },
  { value: "sell", label: "sell · 卖" },
];

const PLACE_ORDER_OPTIONS: { value: PlaceOrderBehavior; label: string }[] = [
  { value: "chaser_open", label: "chaser_open · 追单只挂" },
  { value: "chaser", label: "chaser · 追单成交" },
  { value: "normal", label: "normal · 常规" },
];

export function draftFromPayload(
  payload: StrategyConfigPayload,
  strategyKey: string,
  enabled: boolean,
): Draft {
  return {
    strategy_key: strategyKey,
    enabled,
    symbol: payload.symbol,
    timeframe: payload.timeframe,
    data_source: payload.data_source,
    signal_type: payload.signal_type,
    atr_multiple: String(payload.atr_multiple),
    period: String(payload.period),
    signal_kind: payload.signal_kind,
    position_side: payload.position_side,
    master_side: payload.master_side,
    per_order_amount: String(payload.per_order_amount),
    per_order_unit: payload.per_order_unit,
    entry_mode: payload.entry_mode,
    grid_spacing_rate:
      payload.grid_spacing_rate === null
        ? ""
        : String(payload.grid_spacing_rate),
    trend_spacing_rate:
      payload.trend_spacing_rate === null
        ? ""
        : String(payload.trend_spacing_rate),
    max_order: String(payload.max_order),
    highest_price: String(payload.highest_price),
    lowest_price: String(payload.lowest_price),
    enable_signal_take_profit: payload.enable_signal_take_profit,
    enable_signal_stop_loss: payload.enable_signal_stop_loss,
    enable_single_layer_signal_stop_loss:
      payload.enable_single_layer_signal_stop_loss,
    fixed_rate_take_profit: payload.fixed_rate_take_profit,
    take_profit_use_limit_order: payload.take_profit_use_limit_order,
    fixed_take_profit_rate: String(payload.fixed_take_profit_rate),
    place_order_behavior: payload.place_order_behavior,
    enable_max_order_stop_loss: payload.enable_max_order_stop_loss,
    paused_after_stop_loss: payload.paused_after_stop_loss,
    enable_order_stop_loss: payload.enable_order_stop_loss,
    order_stop_loss_rate: String(payload.order_stop_loss_rate),
    enable_trailing_stop: payload.enable_trailing_stop,
    trailing_stop_rate: String(payload.trailing_stop_rate),
    trailing_stop_activation_profit_rate: String(
      payload.trailing_stop_activation_profit_rate,
    ),
  };
}

function parseNumber(label: string, raw: string): number {
  const trimmed = raw.trim();
  if (trimmed === "") {
    throw new Error(`${label} 不能为空`);
  }
  const value = Number(trimmed);
  if (!Number.isFinite(value)) {
    throw new Error(`${label} 必须是数字`);
  }
  return value;
}

function parseInteger(label: string, raw: string): number {
  const value = parseNumber(label, raw);
  if (!Number.isInteger(value)) {
    throw new Error(`${label} 必须是整数`);
  }
  return value;
}

/** 开关关闭时隐藏的数值字段: 允许为空/非法, 落库用 0 占位（后端不使用） */
function parseInactiveNumber(raw: string): number {
  const trimmed = raw.trim();
  const value = Number(trimmed);
  return trimmed !== "" && Number.isFinite(value) ? value : 0;
}

function signalStopLossModeOf(draft: Draft): SignalStopLossMode {
  if (draft.enable_single_layer_signal_stop_loss) {
    return "single_layer";
  }
  return draft.enable_signal_stop_loss ? "two_layers" : "off";
}

export function payloadFromDraft(draft: Draft): StrategyConfigPayload {
  const isTrendMode = draft.entry_mode === "trend";
  const gridSpacingRate = isTrendMode
    ? null
    : parseNumber("网格间距", draft.grid_spacing_rate);
  const trendSpacingRate = isTrendMode
    ? parseNumber("顺势间距", draft.trend_spacing_rate)
    : null;
  const stopLossMode = signalStopLossModeOf(draft);
  return {
    symbol: draft.symbol.trim(),
    timeframe: draft.timeframe.trim(),
    data_source: draft.data_source,
    signal_type: draft.signal_type,
    atr_multiple: parseNumber("ATR 倍数", draft.atr_multiple),
    period: parseInteger("周期参数", draft.period),
    signal_kind: draft.signal_kind,
    position_side: draft.position_side,
    master_side: draft.master_side,
    per_order_amount: parseNumber("单层规模", draft.per_order_amount),
    per_order_unit: draft.per_order_unit,
    entry_mode: draft.entry_mode,
    grid_spacing_rate: gridSpacingRate,
    trend_spacing_rate: trendSpacingRate,
    max_order: parseInteger("最大层数", draft.max_order),
    highest_price: parseNumber("价格上限", draft.highest_price),
    lowest_price: parseNumber("价格下限", draft.lowest_price),
    enable_signal_take_profit: draft.enable_signal_take_profit,
    enable_signal_stop_loss: stopLossMode !== "off",
    enable_single_layer_signal_stop_loss: stopLossMode === "single_layer",
    fixed_rate_take_profit: draft.fixed_rate_take_profit,
    take_profit_use_limit_order: draft.take_profit_use_limit_order,
    fixed_take_profit_rate: draft.fixed_rate_take_profit
      ? parseNumber("固定止盈率", draft.fixed_take_profit_rate)
      : parseInactiveNumber(draft.fixed_take_profit_rate),
    place_order_behavior: draft.place_order_behavior,
    enable_max_order_stop_loss: draft.enable_max_order_stop_loss,
    paused_after_stop_loss: draft.paused_after_stop_loss,
    enable_order_stop_loss: draft.enable_order_stop_loss,
    order_stop_loss_rate: draft.enable_order_stop_loss
      ? parseNumber("单笔止损率", draft.order_stop_loss_rate)
      : parseInactiveNumber(draft.order_stop_loss_rate),
    enable_trailing_stop: draft.enable_trailing_stop,
    trailing_stop_rate: draft.enable_trailing_stop
      ? parseNumber("跟踪止损率", draft.trailing_stop_rate)
      : parseInactiveNumber(draft.trailing_stop_rate),
    trailing_stop_activation_profit_rate: draft.enable_trailing_stop
      ? parseNumber(
          "跟踪止损激活盈利率",
          draft.trailing_stop_activation_profit_rate,
        )
      : parseInactiveNumber(draft.trailing_stop_activation_profit_rate),
  };
}

export function ConfigEditor({
  editor,
  formError,
  saving,
  onPatch,
  onCancel,
  onSave,
  showEnabled = true,
}: {
  editor: EditorState;
  formError: string | null;
  saving: boolean;
  onPatch: <K extends keyof Draft>(key: K, value: Draft[K]) => void;
  onCancel: () => void;
  onSave: () => void;
  showEnabled?: boolean;
}) {
  const draft = editor.draft;
  const signalStopLossMode = signalStopLossModeOf(draft);
  return (
    <section className="panel config-editor reveal">
      <div className="panel-head">
        <div>
          <h2 className="panel-title">
            {editor.mode === "create" ? "新建策略实例" : "编辑策略实例"}
          </h2>
          <span className="panel-sub mono">
            {editor.mode === "create"
              ? "CREATE · VERSION 1"
              : `UPDATE · BASE VERSION ${editor.version}`}
          </span>
        </div>
        <span className="panel-note">新增/调参自动热加载；身份字段创建后不可变</span>
      </div>

      <div className="config-form">
        <ConfigSection title="基础">
          <ConfigField label="策略键 · 不可变">
            <input
              type="text"
              value={draft.strategy_key}
              placeholder="signal_trend_zecusdt_1m_short_sell"
              disabled={editor.mode === "edit"}
              onChange={(event) => onPatch("strategy_key", event.target.value)}
            />
          </ConfigField>
          <ConfigField label="交易对 · 创建后不可变">
            <input
              type="text"
              value={draft.symbol}
              placeholder="DOGE/USDT"
              disabled={editor.mode === "edit"}
              onChange={(event) => onPatch("symbol", event.target.value)}
            />
          </ConfigField>
          <ConfigField label="周期 · 创建后不可变">
            <input
              type="text"
              value={draft.timeframe}
              placeholder="1m"
              disabled={editor.mode === "edit"}
              onChange={(event) => onPatch("timeframe", event.target.value)}
            />
          </ConfigField>
          <ConfigField label="数据源 · 创建后不可变">
            <SelectInput
              value={draft.data_source}
              options={DATA_SOURCE_OPTIONS}
              disabled={editor.mode === "edit"}
              onChange={(value) => onPatch("data_source", value)}
            />
          </ConfigField>
        </ConfigSection>

        <ConfigSection title="方向">
          <ConfigField label="持仓方向 · 创建后不可变">
            <SelectInput
              value={draft.position_side}
              options={POSITION_SIDE_OPTIONS}
              disabled={editor.mode === "edit"}
              onChange={(value) => onPatch("position_side", value)}
            />
          </ConfigField>
          <ConfigField label="入场方向 · 创建后不可变">
            <SelectInput
              value={draft.master_side}
              options={MASTER_SIDE_OPTIONS}
              disabled={editor.mode === "edit"}
              onChange={(value) => onPatch("master_side", value)}
            />
          </ConfigField>
        </ConfigSection>

        <ConfigSection title="加仓间距">
          <ConfigField label="加仓模式">
            <SelectInput
              value={draft.entry_mode}
              options={ENTRY_MODE_OPTIONS}
              onChange={(value) => {
                onPatch("entry_mode", value);
                onPatch(
                  value === "grid" ? "trend_spacing_rate" : "grid_spacing_rate",
                  "",
                );
              }}
            />
          </ConfigField>
          {draft.entry_mode === "grid" ? (
            <ConfigField label="网格间距">
              <input
                type="text"
                inputMode="decimal"
                value={draft.grid_spacing_rate}
                onChange={(event) =>
                  onPatch("grid_spacing_rate", event.target.value)
                }
              />
            </ConfigField>
          ) : (
            <ConfigField label="顺势间距">
              <input
                type="text"
                inputMode="decimal"
                value={draft.trend_spacing_rate}
                onChange={(event) =>
                  onPatch("trend_spacing_rate", event.target.value)
                }
              />
            </ConfigField>
          )}
          <ConfigField label="单层规模">
            <input
              type="text"
              inputMode="decimal"
              value={draft.per_order_amount}
              onChange={(event) =>
                onPatch("per_order_amount", event.target.value)
              }
            />
          </ConfigField>
          <ConfigField label="规模单位">
            <SelectInput
              value={draft.per_order_unit}
              options={ORDER_SIZE_UNIT_OPTIONS}
              onChange={(value) => onPatch("per_order_unit", value)}
            />
          </ConfigField>
          <ConfigField label="最大层数">
            <input
              type="text"
              inputMode="numeric"
              value={draft.max_order}
              onChange={(event) => onPatch("max_order", event.target.value)}
            />
          </ConfigField>
          <ConfigField label="价格上限">
            <input
              type="text"
              inputMode="decimal"
              value={draft.highest_price}
              onChange={(event) => onPatch("highest_price", event.target.value)}
            />
          </ConfigField>
          <ConfigField label="价格下限">
            <input
              type="text"
              inputMode="decimal"
              value={draft.lowest_price}
              onChange={(event) => onPatch("lowest_price", event.target.value)}
            />
          </ConfigField>
        </ConfigSection>

        <ConfigSection title="信号">
          <ConfigField label="信号实现">
            <SelectInput
              value={draft.signal_type}
              options={SIGNAL_TYPE_OPTIONS}
              onChange={(value) => onPatch("signal_type", value)}
            />
          </ConfigField>
          <ConfigField label="信号解读">
            <SelectInput
              value={draft.signal_kind}
              options={SIGNAL_KIND_OPTIONS}
              onChange={(value) => onPatch("signal_kind", value)}
            />
          </ConfigField>
          {draft.signal_type !== "none" ? (
            <>
              <ConfigField label="ATR 倍数">
                <input
                  type="text"
                  inputMode="decimal"
                  value={draft.atr_multiple}
                  onChange={(event) =>
                    onPatch("atr_multiple", event.target.value)
                  }
                />
              </ConfigField>
              <ConfigField label="指标周期">
                <input
                  type="text"
                  inputMode="numeric"
                  value={draft.period}
                  onChange={(event) => onPatch("period", event.target.value)}
                />
              </ConfigField>
            </>
          ) : null}
        </ConfigSection>

        <ConfigSection title="止盈行为">
          <ConfigBehavior
            control={
              <Toggle
                label="信号止盈"
                checked={draft.enable_signal_take_profit}
                onChange={(checked) =>
                  onPatch("enable_signal_take_profit", checked)
                }
              />
            }
          />
          <ConfigBehavior
            control={
              <Toggle
                label="固定比例止盈"
                checked={draft.fixed_rate_take_profit}
                onChange={(checked) =>
                  onPatch("fixed_rate_take_profit", checked)
                }
              />
            }
          >
            {draft.fixed_rate_take_profit ? (
              <>
                <ConfigField label="固定止盈率">
                  <input
                    type="text"
                    inputMode="decimal"
                    value={draft.fixed_take_profit_rate}
                    onChange={(event) =>
                      onPatch("fixed_take_profit_rate", event.target.value)
                    }
                  />
                </ConfigField>
                <Toggle
                  label="止盈用限价单"
                  checked={draft.take_profit_use_limit_order}
                  onChange={(checked) =>
                    onPatch("take_profit_use_limit_order", checked)
                  }
                />
              </>
            ) : null}
          </ConfigBehavior>
        </ConfigSection>

        <ConfigSection title="止损行为">
          <ConfigBehavior
            control={
              <ConfigField label="信号止损">
                <SelectInput
                  value={signalStopLossMode}
                  options={SIGNAL_STOP_LOSS_MODE_OPTIONS}
                  onChange={(value) => {
                    onPatch("enable_signal_stop_loss", value !== "off");
                    onPatch(
                      "enable_single_layer_signal_stop_loss",
                      value === "single_layer",
                    );
                  }}
                />
              </ConfigField>
            }
          />
          <ConfigBehavior
            control={
              <Toggle
                label="单笔止损"
                checked={draft.enable_order_stop_loss}
                onChange={(checked) =>
                  onPatch("enable_order_stop_loss", checked)
                }
              />
            }
          >
            {draft.enable_order_stop_loss ? (
              <ConfigField label="单笔止损率">
                <input
                  type="text"
                  inputMode="decimal"
                  value={draft.order_stop_loss_rate}
                  onChange={(event) =>
                    onPatch("order_stop_loss_rate", event.target.value)
                  }
                />
              </ConfigField>
            ) : null}
          </ConfigBehavior>
          <ConfigBehavior
            control={
              <Toggle
                label="达到最大层数全平"
                checked={draft.enable_max_order_stop_loss}
                onChange={(checked) =>
                  onPatch("enable_max_order_stop_loss", checked)
                }
              />
            }
          >
            {draft.enable_max_order_stop_loss ? (
              <Toggle
                label="全平后暂停策略"
                checked={draft.paused_after_stop_loss}
                onChange={(checked) =>
                  onPatch("paused_after_stop_loss", checked)
                }
              />
            ) : null}
          </ConfigBehavior>
          <ConfigBehavior
            control={
              <Toggle
                label="跟踪止损"
                checked={draft.enable_trailing_stop}
                onChange={(checked) => onPatch("enable_trailing_stop", checked)}
              />
            }
          >
            {draft.enable_trailing_stop ? (
              <>
                <ConfigField label="跟踪止损率">
                  <input
                    type="text"
                    inputMode="decimal"
                    value={draft.trailing_stop_rate}
                    onChange={(event) =>
                      onPatch("trailing_stop_rate", event.target.value)
                    }
                  />
                </ConfigField>
                <ConfigField label="跟踪激活盈利率">
                  <input
                    type="text"
                    inputMode="decimal"
                    value={draft.trailing_stop_activation_profit_rate}
                    onChange={(event) =>
                      onPatch(
                        "trailing_stop_activation_profit_rate",
                        event.target.value,
                      )
                    }
                  />
                </ConfigField>
              </>
            ) : null}
          </ConfigBehavior>
        </ConfigSection>

        <ConfigSection title="执行">
          <ConfigField label="下单方式">
            <SelectInput
              value={draft.place_order_behavior}
              options={PLACE_ORDER_OPTIONS}
              onChange={(value) => onPatch("place_order_behavior", value)}
            />
          </ConfigField>
        </ConfigSection>

        {showEnabled ? (
          <ConfigSection title="启用">
            <ConfigBehavior
              control={
                <Toggle
                  label="启用 · 启动时加载"
                  checked={draft.enabled}
                  onChange={(checked) => onPatch("enabled", checked)}
                />
              }
            />
          </ConfigSection>
        ) : null}

        <div className="config-form-foot">
          <button
            type="button"
            className="button button-amber"
            onClick={onSave}
            disabled={saving}
          >
            {saving ? "保存中" : editor.mode === "create" ? "创建" : "保存"}
          </button>
          <button
            type="button"
            className="button"
            onClick={onCancel}
            disabled={saving}
          >
            取消
          </button>
          {formError ? <span className="config-error">{formError}</span> : null}
        </div>
      </div>
    </section>
  );
}

function ConfigSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="config-section">
      <div className="config-section-title">{title}</div>
      <div className="config-grid">{children}</div>
    </div>
  );
}

/** 一个独立行为占满整行; 依赖参数缩进挂在行为下方 */
function ConfigBehavior({
  control,
  children,
}: {
  control: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="config-behavior">
      {control}
      {children ? <div className="config-behavior-body">{children}</div> : null}
    </div>
  );
}

function ConfigField({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label className="config-field">
      <span>{label}</span>
      {children}
    </label>
  );
}

function SelectInput<T extends string>({
  value,
  options,
  onChange,
  disabled = false,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  disabled?: boolean;
}) {
  return (
    <select
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value as T)}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="config-toggle">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>{label}</span>
    </label>
  );
}
