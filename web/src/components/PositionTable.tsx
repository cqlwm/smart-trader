import { useState } from "react";

import { usePositionDetail } from "../api/hooks";
import type { OrderStatus, OrderView, PositionView } from "../api/types";
import {
  EmptyState,
  ErrorPanel,
  LoadingBlock,
  Pill,
  PnL,
} from "../components/ui";
import {
  closeReasonLabel,
  formatNumber,
  formatRate,
  formatUtc,
  orderStatusText,
  phaseLabel,
} from "../utils/format";

const phaseVariant: Record<
  PositionView["phase"],
  "green" | "amber" | "blue" | "dim"
> = {
  opening: "amber",
  holding: "green",
  closing: "blue",
  closed: "dim",
};

function statusVariant(
  order: OrderView,
): "green" | "amber" | "blue" | "red" | "dim" {
  if (order.unconfirmed || order.status === null) {
    return "amber";
  }
  const map: Record<OrderStatus, "green" | "blue" | "red" | "dim"> = {
    open: "blue",
    closed: "green",
    canceled: "dim",
    rejected: "red",
    expired: "dim",
  };
  return map[order.status];
}

export function PositionTable({
  positions,
  emptyTitle,
  emptyHint,
}: {
  positions: PositionView[];
  emptyTitle: string;
  emptyHint: string;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);

  if (positions.length === 0) {
    return <EmptyState title={emptyTitle} hint={emptyHint} />;
  }

  return (
    <section className="panel reveal">
      <div className="panel-head">
        <div>
          <h2 className="panel-title">仓位明细</h2>
          <span className="panel-sub mono">
            POSITION LAYERS · {positions.length}
          </span>
        </div>
        <span className="panel-note">点击行展开订单</span>
      </div>

      <div className="table-scroll">
        <table className="data-table position-table">
          <thead>
            <tr>
              <th>仓位 / POSITION</th>
              <th>策略 / SYMBOL</th>
              <th>方向</th>
              <th>相位</th>
              <th className="num">数量</th>
              <th className="num">均价</th>
              <th className="num">已实现盈亏</th>
              <th>风控线</th>
              <th>挂单</th>
              <th>更新 · UTC</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((position, index) => (
              <PositionRows
                key={position.id}
                position={position}
                index={index}
                isOpen={expanded === position.id}
                onToggle={() =>
                  setExpanded((current) =>
                    current === position.id ? null : position.id,
                  )
                }
              />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function PositionRows({
  position,
  index,
  isOpen,
  onToggle,
}: {
  position: PositionView;
  index: number;
  isOpen: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <tr
        className={`clickable reveal ${isOpen ? "is-expanded" : ""}`}
        style={{ animationDelay: `${index * 30}ms` }}
        onClick={onToggle}
      >
        <td>
          <div className="cell-stack">
            <button
              type="button"
              className="expand-button"
              aria-expanded={isOpen}
              onClick={(event) => {
                event.stopPropagation();
                onToggle();
              }}
            >
              <span className="expand-mark">{isOpen ? "−" : "+"}</span>
              <span className="mono cell-primary">{position.id}</span>
            </button>
            <span className="cell-sub mono">
              {formatUtc(position.created_at)}
            </span>
          </div>
        </td>
        <td>
          <div className="cell-stack">
            <span className="mono cell-primary">{position.strategy_key}</span>
            <span className="cell-sub">{position.symbol}</span>
          </div>
        </td>
        <td>
          <Pill variant={position.position_side === "long" ? "green" : "red"}>
            {position.position_side === "long" ? "多 LONG" : "空 SHORT"}
          </Pill>
        </td>
        <td>
          <div className="cell-stack">
            <Pill variant={phaseVariant[position.phase]}>
              {phaseLabel[position.phase]}
            </Pill>
            {position.closed_reason ? (
              <span className="cell-sub">
                {closeReasonLabel[position.closed_reason]}
              </span>
            ) : null}
          </div>
        </td>
        <td className="num">
          <div className="cell-stack align-end">
            <span className="mono cell-primary">
              {formatNumber(position.quantity)}
            </span>
            <span className="cell-sub mono">
              待入场 {formatNumber(position.entry_pending)}
            </span>
          </div>
        </td>
        <td className="num mono">{formatNumber(position.price)}</td>
        <td className="num">
          <PnL value={position.realized_pnl} />
        </td>
        <td>
          <div className="risk-cell">
            <span className="mono">
              止损 <b>{formatNumber(position.stop_loss_price)}</b>
            </span>
            <span className="cell-sub">
              {position.enable_stop_loss
                ? position.enable_trailing_stop
                  ? `跟踪 ${formatRate(position.trailing_stop_rate)}`
                  : `固定 ${formatRate(position.stop_loss_rate)}`
                : "未启用"}
            </span>
            <span className="mono">
              止盈 <b>{formatNumber(position.take_profit_price)}</b>
            </span>
            <span className="cell-sub">
              固定档 {formatRate(position.fixed_take_profit_rate)}
            </span>
          </div>
        </td>
        <td>
          <div className="cell-stack">
            <span className="mono">
              {position.open_order_count} / {position.order_count}
            </span>
            <span className="cell-sub mono">
              {position.pending_exit
                ? position.pending_exit.order_id
                : "无出场挂单"}
            </span>
          </div>
        </td>
        <td className="mono dim">{formatUtc(position.updated_at)}</td>
      </tr>
      {isOpen ? (
        <tr className="detail-row">
          <td colSpan={10}>
            <PositionOrders positionId={position.id} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function PositionOrders({ positionId }: { positionId: string }) {
  const detail = usePositionDetail(positionId);

  if (detail.isPending) {
    return <LoadingBlock label="读取该仓位订单" />;
  }
  if (detail.isError) {
    return (
      <ErrorPanel
        message={
          detail.error instanceof Error
            ? detail.error.message
            : String(detail.error)
        }
        onRetry={() => {
          void detail.refetch();
        }}
      />
    );
  }
  if (!detail.data) {
    return null;
  }

  return (
    <div className="detail-panel">
      <div className="detail-head">
        <span className="mono">订单流水 · {detail.data.orders.length} 笔</span>
        <span className="mono dim">
          入场 {formatNumber(detail.data.entry_filled)} · 出场{" "}
          {formatNumber(detail.data.exit_filled)}
        </span>
      </div>
      {detail.data.orders.length === 0 ? (
        <EmptyState title="该仓位还没有订单" hint="账本只记录已发出的委托。" />
      ) : (
        <div className="table-scroll">
          <table className="data-table nested-table">
            <thead>
              <tr>
                <th>时间 · UTC</th>
                <th>订单号</th>
                <th>方向</th>
                <th>角色</th>
                <th>类型</th>
                <th className="num">委托价</th>
                <th className="num">数量</th>
                <th className="num">成交</th>
                <th className="num">均价</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {detail.data.orders.map((order) => (
                <tr
                  key={order.order_id}
                  className={order.unconfirmed ? "is-wal" : ""}
                >
                  <td className="mono dim">{formatUtc(order.created_at)}</td>
                  <td className="mono">{order.order_id}</td>
                  <td>
                    <span
                      className={
                        order.side === "buy" ? "side-buy" : "side-sell"
                      }
                    >
                      {order.side === "buy" ? "买 BUY" : "卖 SELL"}
                    </span>
                  </td>
                  <td>
                    {order.role ? (
                      <Pill variant={order.role === "entry" ? "blue" : "amber"}>
                        {order.role === "entry" ? "入场 IN" : "出场 OUT"}
                      </Pill>
                    ) : (
                      <span className="dim">—</span>
                    )}
                  </td>
                  <td className="mono dim">{order.type}</td>
                  <td className="num mono">{formatNumber(order.price)}</td>
                  <td className="num mono">{formatNumber(order.quantity)}</td>
                  <td className="num mono">{formatNumber(order.filled_qty)}</td>
                  <td className="num mono">{formatNumber(order.avg_price)}</td>
                  <td>
                    <Pill variant={statusVariant(order)}>
                      {orderStatusText(order.status, order.unconfirmed)}
                    </Pill>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
