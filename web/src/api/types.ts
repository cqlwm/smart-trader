export type PositionSide = "long" | "short";
export type OrderSide = "buy" | "sell";
export type OrderType = "limit" | "market";
export type OrderStatus = "open" | "closed" | "canceled" | "rejected" | "expired";
export type PositionPhase = "opening" | "holding" | "closing" | "closed";
export type OrderRole = "entry" | "exit";
export type PositionCloseReason =
  | "exit_settled"
  | "external_reconciled"
  | "orphan_reaped"
  | "entry_discarded";

export interface OrderView {
  order_id: string;
  grid_position_id: string;
  strategy_key: string;
  symbol: string;
  side: OrderSide;
  type: OrderType;
  role: OrderRole | null;
  price: number | null;
  quantity: number;
  filled_qty: number;
  avg_price: number | null;
  status: OrderStatus | null;
  unconfirmed: boolean;
  created_at: string;
  updated_at: string;
}

export interface PositionView {
  id: string;
  strategy_key: string;
  symbol: string;
  position_side: PositionSide;
  main_order_side: OrderSide;
  phase: PositionPhase;
  active: boolean;
  closed_reason: PositionCloseReason | null;
  quantity: number;
  price: number;
  entry_filled: number;
  entry_pending: number;
  exit_filled: number;
  realized_pnl: number;
  opening_quantity: number;
  opening_price: number;
  fixed_take_profit_rate: number;
  signal_min_take_profit_rate: number;
  enable_stop_loss: boolean;
  stop_loss_rate: number;
  enable_trailing_stop: boolean;
  trailing_stop_rate: number;
  trailing_stop_activation_rate: number;
  trailing_stop_price: number | null;
  stop_loss_price: number | null;
  take_profit_price: number | null;
  pending_exit: OrderView | null;
  order_count: number;
  open_order_count: number;
  created_at: string;
  updated_at: string;
}

export interface PositionDetailView extends PositionView {
  orders: OrderView[];
}

export interface PositionPage {
  items: PositionView[];
  total: number;
  limit: number;
  offset: number;
}

export interface ReconcileView {
  checked_at: string | null;
  exchange_qty: number | null;
  local_qty: number | null;
  gap: number;
  tracked_exit_orders: number;
  max_exit_query_failures: number;
  failing_exit_orders: string[];
}

export type PositionAckStatus = "active" | "revoked";

export interface PositionAckView {
  id: string;
  strategy_key: string;
  symbol: string;
  position_side: PositionSide;
  external_qty: number;
  note: string;
  status: PositionAckStatus;
  acknowledged_by: string;
  acknowledged_at: string;
  revoked_at: string | null;
  revoked_by: string | null;
}

export interface PositionAckPage {
  items: PositionAckView[];
  total: number;
  limit: number;
  offset: number;
}

export interface StrategySummaryView {
  strategy_key: string;
  symbol: string;
  position_side: PositionSide;
  timeframe: string | null;
  active_positions: number;
  archived_positions: number;
  realized_pnl: number;
  open_orders: number;
  unconfirmed_orders: number;
  last_updated_at: string | null;
  alive: boolean;
  running: boolean | null;
  heartbeat_at: string | null;
  reconcile: ReconcileView | null;
  ack: PositionAckView | null;
}

export interface AlertView {
  severity: "critical" | "warning" | "info";
  code: string;
  message: string;
  strategy_key: string | null;
  order_id: string | null;
  created_at: string | null;
}

export interface OverviewView {
  generated_at: string;
  strategy_count: number;
  active_position_count: number;
  archived_position_count: number;
  open_order_count: number;
  unconfirmed_order_count: number;
  realized_pnl_total: number;
  realized_pnl_active: number;
  strategies: StrategySummaryView[];
  alerts: AlertView[];
}

export interface OrderPage {
  items: OrderView[];
  total: number;
  limit: number;
  offset: number;
}

export interface HealthView {
  status: "ok";
  database: string;
  server_time: string;
}

export type CommandAction =
  | "pause"
  | "resume"
  | "close_all"
  | "cancel_entries"
  | "kill_switch";

export type CommandStatus = "pending" | "running" | "succeeded" | "failed";

export interface CommandView {
  id: string;
  strategy_key: string | null;
  action: CommandAction;
  status: CommandStatus;
  result: string | null;
  requested_by: string;
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface CommandPage {
  items: CommandView[];
  total: number;
  limit: number;
  offset: number;
}

export type MarketDirection = "long" | "short" | "neutral";

export interface CandleView {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  finished: boolean;
}

export interface IndicatorPoint {
  timestamp: number;
  alpha_value: number | null;
  trend: number;
  is_breakout: boolean;
}

export interface MarketChartView {
  symbol: string;
  timeframe: string;
  fetched_at: string;
  candles: CandleView[];
  indicator: IndicatorPoint[];
  direction: MarketDirection;
  trend: number;
  last_price: number;
  last_breakout_timestamp: number | null;
}

export type DataSource = "ws" | "timer";
export type StrategySignalType =
  | "alpha_trend"
  | "alpha_trend_inverse"
  | "none";
export type EntryMode = "grid" | "trend";
export type SignalKind = "trigger" | "directional";
export type OrderSizeUnit = "base" | "quote";
export type PlaceOrderBehavior = "chaser" | "chaser_open" | "normal";

export interface StrategyConfigPayload {
  symbol: string;
  timeframe: string;
  data_source: DataSource;
  signal_type: StrategySignalType;
  atr_multiple: number;
  period: number;
  signal_kind: SignalKind;
  position_side: PositionSide;
  master_side: OrderSide;
  per_order_amount: number;
  per_order_unit: OrderSizeUnit;
  entry_mode: EntryMode;
  grid_spacing_rate: number | null;
  trend_spacing_rate: number | null;
  max_order: number;
  highest_price: number;
  lowest_price: number;
  enable_signal_take_profit: boolean;
  enable_signal_stop_loss: boolean;
  enable_single_layer_signal_stop_loss: boolean;
  fixed_rate_take_profit: boolean;
  take_profit_use_limit_order: boolean;
  fixed_take_profit_rate: number;
  place_order_behavior: PlaceOrderBehavior;
  enable_max_order_stop_loss: boolean;
  paused_after_stop_loss: boolean;
  enable_order_stop_loss: boolean;
  order_stop_loss_rate: number;
  enable_trailing_stop: boolean;
  trailing_stop_rate: number;
  trailing_stop_activation_profit_rate: number;
}

export interface StrategyConfigView {
  strategy_key: string;
  payload: StrategyConfigPayload;
  version: number;
  enabled: boolean;
  updated_by: string;
  created_at: string;
  updated_at: string;
}

export interface StrategyConfigCreateRequest {
  strategy_key: string;
  enabled: boolean;
  payload: StrategyConfigPayload;
}

export interface StrategyConfigUpdateRequest {
  enabled: boolean;
  expected_version: number;
  payload: StrategyConfigPayload;
}
