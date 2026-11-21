import { useState } from "react";

import { useOrders } from "../api/hooks";
import type { OrderStatus } from "../api/types";
import {
  EmptyState,
  ErrorPanel,
  LoadingBlock,
  Pill,
} from "../components/ui";
import { formatNumber, formatUtc, orderStatusText } from "../utils/format";

type StatusFilter = OrderStatus | "unconfirmed" | "";

const LIMIT = 50;

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "", label: "全部" },
  { value: "open", label: "挂单" },
  { value: "unconfirmed", label: "未确认" },
  { value: "closed", label: "成交" },
  { value: "canceled", label: "已撤" },
  { value: "rejected", label: "拒单" },
  { value: "expired", label: "过期" },
];

const statusVariant = {
  open: "blue",
  closed: "green",
  canceled: "dim",
  rejected: "red",
  expired: "dim",
  unconfirmed: "amber",
} as const;

export function OrderTable({ strategyKey }: { strategyKey: string }) {
  const [status, setStatus] = useState<StatusFilter>("");
  const [offset, setOffset] = useState(0);

  const orders = useOrders(strategyKey, status, LIMIT, offset);

  const total = orders.data?.total ?? 0;
  const page = Math.floor(offset / LIMIT) + 1;
  const totalPages = Math.max(1, Math.ceil(total / LIMIT));

  function changeStatus(next: StatusFilter) {
    setStatus(next);
    setOffset(0);
  }

  if (orders.isPending) {
    return <LoadingBlock label="读取订单流水中" />;
  }
  if (orders.isError) {
    return (
      <ErrorPanel
        message={
          orders.error instanceof Error
            ? orders.error.message
            : String(orders.error)
        }
        onRetry={() => {
          void orders.refetch();
        }}
      />
    );
  }
  if (!orders.data) {
    return null;
  }

  return (
    <>
      <section className="toolbar reveal" aria-label="订单状态筛选">
        <div className="chip-row">
          {STATUS_FILTERS.map((filter) => (
            <button
              key={filter.value || "all"}
              type="button"
              className={`chip ${status === filter.value ? "is-active" : ""} ${
                filter.value === "unconfirmed" ? "chip-wal" : ""
              }`}
              onClick={() => changeStatus(filter.value)}
            >
              {filter.label}
            </button>
          ))}
        </div>
        <span className="toolbar-note mono">ORDER BY CREATED_AT DESC</span>
      </section>

      {orders.data.items.length === 0 ? (
        <EmptyState
          title="没有匹配的订单"
          hint="调整状态筛选；未确认订单在 status=unconfirmed 下查看。"
        />
      ) : (
        <section className="panel reveal">
          <div className="panel-head">
            <div>
              <h2 className="panel-title">委托流水</h2>
              <span className="panel-sub mono">
                ORDER JOURNAL · {orders.data.items.length} ROWS
              </span>
            </div>
            <span className="panel-note">WAL 行以琥珀色标记</span>
          </div>

          <div className="table-scroll">
            <table className="data-table orders-table">
              <thead>
                <tr>
                  <th>时间 · UTC</th>
                  <th>订单号 / ORDER ID</th>
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
                {orders.data.items.map((order, index) => (
                  <tr
                    key={order.order_id}
                    className={`${order.unconfirmed ? "is-wal" : ""} reveal`}
                    style={{ animationDelay: `${index * 20}ms` }}
                  >
                    <td className="mono dim">{formatUtc(order.created_at)}</td>
                    <td>
                      <span className="mono cell-primary">
                        {order.order_id}
                      </span>
                    </td>
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
                        <Pill
                          variant={order.role === "entry" ? "blue" : "amber"}
                        >
                          {order.role === "entry" ? "入场 IN" : "出场 OUT"}
                        </Pill>
                      ) : (
                        <span className="dim">—</span>
                      )}
                    </td>
                    <td className="mono dim">{order.type}</td>
                    <td className="num mono">{formatNumber(order.price)}</td>
                    <td className="num mono">{formatNumber(order.quantity)}</td>
                    <td className="num mono">
                      {formatNumber(order.filled_qty)}
                    </td>
                    <td className="num mono">{formatNumber(order.avg_price)}</td>
                    <td>
                      <Pill
                        variant={
                          order.unconfirmed || order.status === null
                            ? statusVariant.unconfirmed
                            : statusVariant[order.status]
                        }
                      >
                        {orderStatusText(order.status, order.unconfirmed)}
                      </Pill>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="pagination">
            <span className="mono dim">
              OFFSET {offset} · LIMIT {LIMIT}
            </span>
            <div className="pagination-controls">
              <button
                type="button"
                className="button"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - LIMIT))}
              >
                上一页
              </button>
              <span className="mono">
                {page} / {totalPages}
              </span>
              <button
                type="button"
                className="button"
                disabled={offset + LIMIT >= total}
                onClick={() => setOffset(offset + LIMIT)}
              >
                下一页
              </button>
            </div>
            <span className="mono dim">{total} ROWS</span>
          </div>
        </section>
      )}
    </>
  );
}
