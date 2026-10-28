import type {
  CommandAction,
  CommandStatus,
  OrderSide,
  OrderStatus,
  PositionCloseReason,
  PositionPhase,
  PositionSide,
} from "../api/types";

const numberFormat = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 6,
  minimumFractionDigits: 0,
});

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  return numberFormat.format(value);
}

export function formatSigned(value: number): string {
  if (!Number.isFinite(value)) {
    return "—";
  }
  const sign = value > 0 ? "+" : "";
  return `${sign}${numberFormat.format(value)}`;
}

export function formatRate(value: number): string {
  return `${(value * 100).toFixed(4)}%`;
}

export function formatUtc(iso: string | null | undefined): string {
  if (!iso) {
    return "—";
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "UTC",
    year: "2-digit",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  })
    .format(date)
    .replace(",", "");
}

export function formatClock(date: Date): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "UTC",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

export function pnlClass(value: number): string {
  if (value > 0) {
    return "is-positive";
  }
  if (value < 0) {
    return "is-negative";
  }
  return "is-flat";
}

export const sideLabel: Record<OrderSide, string> = {
  buy: "买 / BUY",
  sell: "卖 / SELL",
};

export const positionSideLabel: Record<PositionSide, string> = {
  long: "多 / LONG",
  short: "空 / SHORT",
};

export const phaseLabel: Record<PositionPhase, string> = {
  opening: "开仓中 / OPENING",
  holding: "持有 / HOLDING",
  closing: "平仓中 / CLOSING",
  closed: "已归档 / CLOSED",
};

export const orderStatusLabel: Record<OrderStatus, string> = {
  open: "挂单 OPEN",
  closed: "成交 CLOSED",
  canceled: "已撤 CANCELED",
  rejected: "拒单 REJECTED",
  expired: "过期 EXPIRED",
};

export const closeReasonLabel: Record<PositionCloseReason, string> = {
  exit_settled: "出场结算",
  external_reconciled: "外部平仓对账",
  orphan_reaped: "孤儿回收",
  entry_discarded: "入场单丢弃",
};

export function orderStatusText(
  status: OrderStatus | null,
  unconfirmed: boolean,
): string {
  if (unconfirmed || status === null) {
    return "未确认 WAL";
  }
  return orderStatusLabel[status];
}

export const commandActionLabel: Record<CommandAction, string> = {
  pause: "暂停 PAUSE",
  resume: "恢复 RESUME",
  close_all: "全平 CLOSE ALL",
  cancel_entries: "撤入场 CANCEL ENTRIES",
  kill_switch: "急停 KILL SWITCH",
};

export const commandStatusLabel: Record<CommandStatus, string> = {
  pending: "待执行 PENDING",
  running: "执行中 RUNNING",
  succeeded: "成功 SUCCEEDED",
  failed: "失败 FAILED",
};
