import { useState } from "react";
import type { ReactNode } from "react";

import {
  useConfigDefaults,
  useConfigs,
  useCreateConfig,
  useUpdateConfig,
} from "../api/hooks";
import type {
  DataSource,
  OrderSide,
  OrderSizeUnit,
  PlaceOrderBehavior,
  PositionSide,
  SignalKind,
  StrategyConfigPayload,
  StrategyConfigView,
  StrategySignalType,
} from "../api/types";
import {
  EmptyState,
  ErrorPanel,
  LoadingBlock,
  PageHeader,
  Pill,
  RefreshButton,
} from "../components/ui";
import { formatUtc } from "../utils/format";

interface Draft {
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

interface EditorState {
  mode: "create" | "edit";
  version: number;
  draft: Draft;
}

const DATA_SOURCE_OPTIONS: { value: DataSource; label: string }[] = [
  { value: "timer", label: "timer · REST 定时" },
  { value: "ws", label: "ws · WebSocket" },
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

const MASTER_SIDE_OPTIONS: { value: OrderSide; label: string }[] = [
  { value: "buy", label: "buy · 买" },
  { value: "sell", label: "sell · 卖" },
];

const PLACE_ORDER_OPTIONS: { value: PlaceOrderBehavior; label: string }[] = [
  { value: "chaser_open", label: "chaser_open · 追单只挂" },
  { value: "chaser", label: "chaser · 追单成交" },
  { value: "normal", label: "normal · 常规" },
];

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function draftFromPayload(
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
    grid_spacing_rate: String(payload.grid_spacing_rate),
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

function payloadFromDraft(draft: Draft): StrategyConfigPayload {
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
    grid_spacing_rate: parseNumber("网格间距", draft.grid_spacing_rate),
    trend_spacing_rate:
      draft.trend_spacing_rate.trim() === ""
        ? null
        : parseNumber("顺势间距", draft.trend_spacing_rate),
    max_order: parseInteger("最大层数", draft.max_order),
    highest_price: parseNumber("价格上限", draft.highest_price),
    lowest_price: parseNumber("价格下限", draft.lowest_price),
    enable_signal_take_profit: draft.enable_signal_take_profit,
    enable_signal_stop_loss: draft.enable_signal_stop_loss,
    enable_single_layer_signal_stop_loss:
      draft.enable_single_layer_signal_stop_loss,
    fixed_rate_take_profit: draft.fixed_rate_take_profit,
    take_profit_use_limit_order: draft.take_profit_use_limit_order,
    fixed_take_profit_rate: parseNumber(
      "固定止盈率",
      draft.fixed_take_profit_rate,
    ),
    place_order_behavior: draft.place_order_behavior,
    enable_max_order_stop_loss: draft.enable_max_order_stop_loss,
    paused_after_stop_loss: draft.paused_after_stop_loss,
    enable_order_stop_loss: draft.enable_order_stop_loss,
    order_stop_loss_rate: parseNumber("单笔止损率", draft.order_stop_loss_rate),
    enable_trailing_stop: draft.enable_trailing_stop,
    trailing_stop_rate: parseNumber("跟踪止损率", draft.trailing_stop_rate),
    trailing_stop_activation_profit_rate: parseNumber(
      "跟踪止损激活盈利率",
      draft.trailing_stop_activation_profit_rate,
    ),
  };
}

export function ConfigsPage() {
  const configs = useConfigs();
  const defaults = useConfigDefaults(true);
  const createConfig = useCreateConfig();
  const updateConfig = useUpdateConfig();

  const [editor, setEditor] = useState<EditorState | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const rows = configs.data ?? [];

  function patch<K extends keyof Draft>(key: K, value: Draft[K]) {
    setEditor((current) =>
      current === null
        ? current
        : { ...current, draft: { ...current.draft, [key]: value } },
    );
  }

  function startCreate() {
    const base = defaults.data;
    if (!base) {
      return;
    }
    setFormError(null);
    setActionError(null);
    setEditor({
      mode: "create",
      version: 0,
      draft: draftFromPayload(base, "", true),
    });
  }

  function startEdit(config: StrategyConfigView) {
    setFormError(null);
    setActionError(null);
    setEditor({
      mode: "edit",
      version: config.version,
      draft: draftFromPayload(
        config.payload,
        config.strategy_key,
        config.enabled,
      ),
    });
  }

  async function handleSave() {
    if (editor === null) {
      return;
    }
    setFormError(null);
    let payload: StrategyConfigPayload;
    try {
      payload = payloadFromDraft(editor.draft);
    } catch (error) {
      setFormError(messageOf(error));
      return;
    }

    try {
      if (editor.mode === "create") {
        const key = editor.draft.strategy_key.trim();
        if (!key) {
          setFormError("策略键不能为空");
          return;
        }
        await createConfig.mutateAsync({
          strategy_key: key,
          enabled: editor.draft.enabled,
          payload,
        });
        setNotice(`${key} 已创建；启用后将自动热加载（结构字段与停用仍需重启）。`);
      } else {
        await updateConfig.mutateAsync({
          strategyKey: editor.draft.strategy_key,
          body: {
            enabled: editor.draft.enabled,
            expected_version: editor.version,
            payload,
          },
        });
        setNotice(
          `${editor.draft.strategy_key} 已保存；调参将自动热更新（结构字段仍需重启）。`,
        );
      }
      setEditor(null);
    } catch (error) {
      setFormError(messageOf(error));
    }
  }

  async function handleToggle(config: StrategyConfigView) {
    setActionError(null);
    setNotice(null);
    try {
      await updateConfig.mutateAsync({
        strategyKey: config.strategy_key,
        body: {
          enabled: !config.enabled,
          expected_version: config.version,
          payload: config.payload,
        },
      });
      setNotice(
        config.enabled
          ? `${config.strategy_key} 已停用；重启策略进程后生效。`
          : `${config.strategy_key} 已启用；将自动热加载。`,
      );
    } catch (error) {
      setActionError(`${config.strategy_key}: ${messageOf(error)}`);
    }
  }

  return (
    <>
      <PageHeader
        kicker="05 / CONFIG"
        title="配置"
        description="策略实例参数保存在 strategy_configs；策略进程启动时按数据源加载，运行中不会被热替换。"
        meta={configs.data ? `${rows.length} 个策略实例` : undefined}
        actions={
          <div className="config-head-actions">
            <button
              type="button"
              className="button button-amber"
              onClick={startCreate}
              disabled={defaults.isPending || defaults.isError}
            >
              新建配置
            </button>
            <RefreshButton
              onClick={() => {
                void configs.refetch();
              }}
              busy={configs.isFetching}
            />
          </div>
        }
      />

      <div className="setup-banner reveal">
        <span className="setup-mark">!</span>
        <div>
          <div className="setup-title">新增与调参自动热加载</div>
          <div className="setup-hint">
            运行时每 2 秒读取一次配置：新增策略与调参自动生效；交易对 / 周期 /
            数据源 / 持仓方向 / 入场方向创建后不可修改，停用仍需重启 run.py，
            启用未运行的配置会自动加载。
          </div>
        </div>
      </div>

      {notice ? <div className="config-notice reveal">{notice}</div> : null}
      {actionError ? (
        <div className="config-error config-error-block reveal">{actionError}</div>
      ) : null}

      {editor ? (
        <ConfigEditor
          editor={editor}
          formError={formError}
          saving={createConfig.isPending || updateConfig.isPending}
          onPatch={patch}
          onCancel={() => {
            setEditor(null);
            setFormError(null);
          }}
          onSave={() => {
            void handleSave();
          }}
        />
      ) : null}

      {configs.isPending ? <LoadingBlock label="读取策略配置中" /> : null}
      {configs.isError ? (
        <ErrorPanel
          message={messageOf(configs.error)}
          onRetry={() => {
            void configs.refetch();
          }}
        />
      ) : null}

      {configs.data ? (
        rows.length === 0 ? (
          <EmptyState
            title="还没有策略配置"
            hint="从默认模板新建，或执行 scripts/migrations/004_seed_template_configs.py 播种现有模板。"
          />
        ) : (
          <section className="panel reveal">
            <div className="panel-head">
              <div>
                <h2 className="panel-title">策略实例</h2>
                <span className="panel-sub mono">
                  STRATEGY CONFIGS · {rows.length} ROWS
                </span>
              </div>
              <span className="panel-note">策略键不可变 · 停用代替删除</span>
            </div>

            <div className="table-scroll">
              <table className="data-table config-table">
                <thead>
                  <tr>
                    <th>策略键 / STRATEGY KEY</th>
                    <th>交易对 / 周期</th>
                    <th>数据源</th>
                    <th>方向</th>
                    <th>信号</th>
                    <th>状态</th>
                    <th className="num">版本</th>
                    <th>更新 · UTC</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((config, index) => (
                    <tr
                      key={config.strategy_key}
                      className="reveal"
                      style={{ animationDelay: `${index * 25}ms` }}
                    >
                      <td>
                        <span className="mono cell-primary">
                          {config.strategy_key}
                        </span>
                      </td>
                      <td>
                        <div className="cell-stack">
                          <span className="mono">{config.payload.symbol}</span>
                          <span className="cell-sub mono">
                            {config.payload.timeframe}
                          </span>
                        </div>
                      </td>
                      <td>
                        <Pill
                          variant={
                            config.payload.data_source === "ws" ? "blue" : "amber"
                          }
                        >
                          {config.payload.data_source}
                        </Pill>
                      </td>
                      <td>
                        <div className="cell-stack">
                          <span
                            className={
                              config.payload.position_side === "long"
                                ? "side-buy"
                                : "side-sell"
                            }
                          >
                            {config.payload.position_side === "long"
                              ? "多 LONG"
                              : "空 SHORT"}
                          </span>
                          <span className="cell-sub mono">
                            入场 {config.payload.master_side}
                          </span>
                        </div>
                      </td>
                      <td>
                        <div className="cell-stack">
                          <span className="mono">
                            {config.payload.signal_type}
                          </span>
                          <span className="cell-sub mono">
                            {config.payload.signal_kind}
                          </span>
                        </div>
                      </td>
                      <td>
                        <Pill variant={config.enabled ? "green" : "dim"}>
                          {config.enabled ? "启用" : "停用"}
                        </Pill>
                      </td>
                      <td className="num mono">v{config.version}</td>
                      <td>
                        <div className="cell-stack">
                          <span className="mono dim">
                            {formatUtc(config.updated_at)}
                          </span>
                          <span className="cell-sub">{config.updated_by}</span>
                        </div>
                      </td>
                      <td>
                        <div className="config-actions">
                          <button
                            type="button"
                            className="button"
                            onClick={() => startEdit(config)}
                          >
                            编辑
                          </button>
                          <button
                            type="button"
                            className={`button ${config.enabled ? "button-danger" : ""}`}
                            onClick={() => {
                              void handleToggle(config);
                            }}
                            disabled={updateConfig.isPending}
                          >
                            {config.enabled ? "停用" : "启用"}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )
      ) : null}

      <p className="foot-note reveal">
        交易对 / 周期 / 数据源 / 持仓方向 / 入场方向创建后不可修改；其余参数可改，
        保存后自动热更新。存在活跃仓位时禁止停用。乐观锁版本冲突会提示刷新后重试。
      </p>
    </>
  );
}

function ConfigEditor({
  editor,
  formError,
  saving,
  onPatch,
  onCancel,
  onSave,
}: {
  editor: EditorState;
  formError: string | null;
  saving: boolean;
  onPatch: <K extends keyof Draft>(key: K, value: Draft[K]) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  const draft = editor.draft;
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
          <Toggle
            label="启用 · 启动时加载"
            checked={draft.enabled}
            onChange={(checked) => onPatch("enabled", checked)}
          />
        </ConfigSection>

        <ConfigSection title="方向与仓位">
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
          <ConfigField label="信号解读">
            <SelectInput
              value={draft.signal_kind}
              options={SIGNAL_KIND_OPTIONS}
              onChange={(value) => onPatch("signal_kind", value)}
            />
          </ConfigField>
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
        </ConfigSection>

        <ConfigSection title="加仓间距">
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
          <ConfigField label="顺势间距 · 留空用网格">
            <input
              type="text"
              inputMode="decimal"
              value={draft.trend_spacing_rate}
              onChange={(event) =>
                onPatch("trend_spacing_rate", event.target.value)
              }
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
          <ConfigField label="ATR 倍数">
            <input
              type="text"
              inputMode="decimal"
              value={draft.atr_multiple}
              onChange={(event) => onPatch("atr_multiple", event.target.value)}
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
          <Toggle
            label="信号止盈"
            checked={draft.enable_signal_take_profit}
            onChange={(checked) => onPatch("enable_signal_take_profit", checked)}
          />
          <Toggle
            label="信号止损"
            checked={draft.enable_signal_stop_loss}
            onChange={(checked) => onPatch("enable_signal_stop_loss", checked)}
          />
          <Toggle
            label="单层信号止损（一层浮亏即离场）"
            checked={draft.enable_single_layer_signal_stop_loss}
            onChange={(checked) =>
              onPatch("enable_single_layer_signal_stop_loss", checked)
            }
          />
        </ConfigSection>

        <ConfigSection title="止盈止损">
          <Toggle
            label="固定比例止盈"
            checked={draft.fixed_rate_take_profit}
            onChange={(checked) => onPatch("fixed_rate_take_profit", checked)}
          />
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
          <Toggle
            label="单笔止损"
            checked={draft.enable_order_stop_loss}
            onChange={(checked) => onPatch("enable_order_stop_loss", checked)}
          />
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
          <Toggle
            label="达到上限全止损"
            checked={draft.enable_max_order_stop_loss}
            onChange={(checked) => onPatch("enable_max_order_stop_loss", checked)}
          />
          <Toggle
            label="止损后暂停"
            checked={draft.paused_after_stop_loss}
            onChange={(checked) => onPatch("paused_after_stop_loss", checked)}
          />
          <Toggle
            label="跟踪止损"
            checked={draft.enable_trailing_stop}
            onChange={(checked) => onPatch("enable_trailing_stop", checked)}
          />
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

        <div className="config-form-foot">
          <button
            type="button"
            className="button button-amber"
            onClick={onSave}
            disabled={saving}
          >
            {saving ? "保存中" : editor.mode === "create" ? "创建" : "保存"}
          </button>
          <button type="button" className="button" onClick={onCancel} disabled={saving}>
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
