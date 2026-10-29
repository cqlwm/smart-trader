import { useState } from "react";

import { newIdempotencyKey } from "../api/client";
import {
  useCommands,
  useCreatePositionAck,
  useKillSwitch,
  useOverview,
  useRevokePositionAck,
  useSubmitCommand,
} from "../api/hooks";
import type {
  CommandAction,
  CommandPage,
  CommandStatus,
  CommandView,
  OverviewView,
  PositionAckView,
  StrategySummaryView,
} from "../api/types";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { PositionAckDialog } from "../components/PositionAckDialog";
import {
  EmptyState,
  ErrorPanel,
  LoadingBlock,
  PageHeader,
  Pill,
  PnL,
  RefreshButton,
  StatCard,
} from "../components/ui";
import {
  commandActionLabel,
  commandStatusLabel,
  formatNumber,
  formatSigned,
  formatUtc,
} from "../utils/format";

type PendingAction =
  | { kind: "close_all" | "cancel_entries"; strategyKey: string }
  | { kind: "kill" };

interface Notice {
  tone: "info" | "danger";
  text: string;
}

const commandStatusVariant: Record<
  CommandStatus,
  "amber" | "blue" | "green" | "red"
> = {
  pending: "amber",
  running: "blue",
  succeeded: "green",
  failed: "red",
};

const actionShortLabel: Record<CommandAction, string> = {
  pause: "暂停",
  resume: "恢复",
  close_all: "全平",
  cancel_entries: "撤入场",
  kill_switch: "急停",
};

export function OverviewPage() {
  const overview = useOverview();
  const submitCommand = useSubmitCommand();
  const killSwitch = useKillSwitch();
  const createAck = useCreatePositionAck();
  const revokeAck = useRevokePositionAck();
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [ackTarget, setAckTarget] = useState<{
    strategyKey: string;
    surplus: number;
  } | null>(null);
  const [ackError, setAckError] = useState<string | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<{
    strategyKey: string;
    ackId: string;
    externalQty: number;
  } | null>(null);
  const [revokeError, setRevokeError] = useState<string | null>(null);

  const busy = submitCommand.isPending || killSwitch.isPending;

  function requestAction(action: PendingAction) {
    setDialogError(null);
    setPendingAction(action);
  }

  function closeDialog() {
    if (busy) {
      return;
    }
    setPendingAction(null);
    setDialogError(null);
  }

  async function submitQuick(
    strategyKey: string,
    action: "pause" | "resume",
  ) {
    try {
      const command = await submitCommand.mutateAsync({
        strategyKey,
        action,
        idempotencyKey: newIdempotencyKey(),
      });
      setNotice({
        tone: "info",
        text: `命令 ${command.id}（${commandActionLabel[command.action]}）已提交，等待策略进程执行。`,
      });
      void overview.refetch();
    } catch (error) {
      setNotice({
        tone: "danger",
        text: error instanceof Error ? error.message : String(error),
      });
    }
  }

  async function confirmAction() {
    if (!pendingAction) {
      return;
    }
    setDialogError(null);
    try {
      const command =
        pendingAction.kind === "kill"
          ? await killSwitch.mutateAsync(newIdempotencyKey())
          : await submitCommand.mutateAsync({
              strategyKey: pendingAction.strategyKey,
              action: pendingAction.kind,
              idempotencyKey: newIdempotencyKey(),
            });
      setNotice({
        tone: pendingAction.kind === "kill" ? "danger" : "info",
        text: `命令 ${command.id}（${commandActionLabel[command.action]}）已提交，等待策略进程执行。`,
      });
      setPendingAction(null);
      void overview.refetch();
    } catch (error) {
      setDialogError(error instanceof Error ? error.message : String(error));
    }
  }

  const dialogCopy = pendingAction ? describeAction(pendingAction) : null;

  async function handleCreateAck(externalQty: number, note: string) {
    if (!ackTarget) {
      return;
    }
    setAckError(null);
    try {
      await createAck.mutateAsync({
        strategyKey: ackTarget.strategyKey,
        externalQty,
        note,
      });
      setNotice({
        tone: "info",
        text: `已将 ${externalQty} 标记为人工仓位；差额未变化前不再告警。`,
      });
      setAckTarget(null);
      void overview.refetch();
    } catch (error) {
      setAckError(messageOf(error));
    }
  }

  async function handleRevokeAck() {
    if (!revokeTarget) {
      return;
    }
    setRevokeError(null);
    try {
      await revokeAck.mutateAsync(revokeTarget.ackId);
      setNotice({
        tone: "info",
        text: `已撤销 ${revokeTarget.strategyKey} 的人工仓位标记，差额告警恢复。`,
      });
      setRevokeTarget(null);
      void overview.refetch();
    } catch (error) {
      setRevokeError(messageOf(error));
    }
  }

  return (
    <>
      <PageHeader
        kicker="01 / OVERVIEW"
        title="总览"
        description="账本聚合、策略摘要与控制命令。账本只读派生，控制动作经 commands 表交给策略进程执行。"
        meta={
          overview.data ? `生成于 ${formatUtc(overview.data.generated_at)} · UTC` : undefined
        }
        actions={
          <div className="page-actions">
            <RefreshButton
              onClick={() => {
                void overview.refetch();
              }}
              busy={overview.isFetching}
            />
            <button
              type="button"
              className="button button-danger"
              disabled={busy}
              onClick={() => requestAction({ kind: "kill" })}
            >
              <span className="kill-dot" />
              KILL SWITCH
            </button>
          </div>
        }
      />

      {notice ? (
        <div className={`notice notice-${notice.tone}`} role="status">
          <span className="notice-mark">{notice.tone === "danger" ? "!" : "i"}</span>
          <span className="notice-text">{notice.text}</span>
          <button
            type="button"
            className="notice-close"
            onClick={() => setNotice(null)}
            aria-label="关闭提示"
          >
            ×
          </button>
        </div>
      ) : null}

      {overview.isPending ? <LoadingBlock /> : null}
      {overview.isError ? (
        <ErrorPanel
          message={
            overview.error instanceof Error ? overview.error.message : String(overview.error)
          }
          onRetry={() => {
            void overview.refetch();
          }}
        />
      ) : null}

      {overview.data ? (
        <OverviewBody
          data={overview.data}
          busy={busy}
          onQuick={(strategyKey, action) => {
            void submitQuick(strategyKey, action);
          }}
          onRequest={requestAction}
          onMarkAck={(strategyKey, surplus) => {
            setAckError(null);
            setAckTarget({ strategyKey, surplus });
          }}
          onRevokeAck={(ack) => {
            setRevokeError(null);
            setRevokeTarget({
              strategyKey: ack.strategy_key,
              ackId: ack.id,
              externalQty: ack.external_qty,
            });
          }}
        />
      ) : null}

      {overview.data ? <CommandHistory /> : null}

      <p className="foot-note reveal">
        控制命令只写入 commands 表，由策略进程内的 CommandRunner 认领执行；API 不直接下单、不写仓位或订单。
        暂停只停止新的入场/离场决策，已挂出的出场单仍会被跟踪；若策略进程未运行，命令会保持 PENDING。
      </p>

      {pendingAction && dialogCopy ? (
        <ConfirmDialog
          open
          title={dialogCopy.title}
          description={dialogCopy.description}
          confirmLabel={dialogCopy.confirmLabel}
          tone={dialogCopy.tone}
          busy={busy}
          error={dialogError}
          onConfirm={() => {
            void confirmAction();
          }}
          onClose={closeDialog}
        />
      ) : null}

      {ackTarget ? (
        <PositionAckDialog
          open
          strategyKey={ackTarget.strategyKey}
          surplus={ackTarget.surplus}
          busy={createAck.isPending}
          error={ackError}
          onConfirm={(externalQty, note) => {
            void handleCreateAck(externalQty, note);
          }}
          onClose={() => {
            if (createAck.isPending) {
              return;
            }
            setAckTarget(null);
            setAckError(null);
          }}
        />
      ) : null}

      {revokeTarget ? (
        <ConfirmDialog
          open
          kicker="POSITION / ACK"
          title={`撤销 ${revokeTarget.strategyKey} 的人工仓位标记？`}
          description={`撤销后将重新对差额告警。已声明人工仓位 ${revokeTarget.externalQty}；撤销记录会保留在审计中。`}
          confirmLabel="撤销标记"
          tone="amber"
          busy={revokeAck.isPending}
          error={revokeError}
          onConfirm={() => {
            void handleRevokeAck();
          }}
          onClose={() => {
            if (revokeAck.isPending) {
              return;
            }
            setRevokeTarget(null);
            setRevokeError(null);
          }}
        />
      ) : null}
    </>
  );
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function describeAction(action: PendingAction): {
  title: string;
  description: string;
  confirmLabel: string;
  tone: "danger" | "amber";
} {
  if (action.kind === "kill") {
    return {
      title: "触发全局 KILL SWITCH？",
      description:
        "所有可控策略会先提交全平请求，再暂停决策。已挂出的出场单仍会被跟踪，但不再产生新的入场/离场决策。该命令写入 commands 表，由策略进程执行。",
      confirmLabel: "执行急停",
      tone: "danger",
    };
  }
  if (action.kind === "close_all") {
    return {
      title: `确认全平 ${action.strategyKey}？`,
      description:
        "策略会在下一根完成 K 线消费全平标志：撤掉休息中的出场挂单、结算已成交部分，再按最新收盘价挂出剩余可平量。不会绕过账本与交易所路径。",
      confirmLabel: "确认全平",
      tone: "danger",
    };
  }
  return {
    title: `撤销 ${action.strategyKey} 的入场挂单？`,
    description:
      "只撤销未成交的入场单，不碰出场单。已成交部分保留；归零且无存活订单的仓位会按 ENTRY_DISCARDED 归档。",
    confirmLabel: "确认撤单",
    tone: "amber",
  };
}

function OverviewBody({
  data,
  busy,
  onQuick,
  onRequest,
  onMarkAck,
  onRevokeAck,
}: {
  data: OverviewView;
  busy: boolean;
  onQuick: (strategyKey: string, action: "pause" | "resume") => void;
  onRequest: (action: PendingAction) => void;
  onMarkAck: (strategyKey: string, surplus: number) => void;
  onRevokeAck: (ack: PositionAckView) => void;
}) {
  const pnlTone =
    data.realized_pnl_total > 0
      ? "positive"
      : data.realized_pnl_total < 0
        ? "negative"
        : "neutral";
  const walTone = data.unconfirmed_order_count > 0 ? "amber" : "neutral";

  return (
    <>
      <section className="stat-grid">
        <StatCard
          label="策略总数 / STRATEGIES"
          value={String(data.strategy_count).padStart(2, "0")}
          sub={`活跃 ${data.active_position_count} · 归档 ${data.archived_position_count}`}
          delay={0}
        />
        <StatCard
          label="活跃仓位 / ACTIVE"
          value={String(data.active_position_count).padStart(2, "0")}
          sub={`挂单 ${data.open_order_count} · 未确认 ${data.unconfirmed_order_count}`}
          tone={data.active_position_count > 0 ? "positive" : "neutral"}
          delay={60}
        />
        <StatCard
          label="已实现盈亏 / REALIZED"
          value={formatSigned(data.realized_pnl_total)}
          sub={`活跃仓位 ${formatSigned(data.realized_pnl_active)}`}
          tone={pnlTone}
          delay={120}
        />
        <StatCard
          label="WAL 未确认 / UNCONFIRMED"
          value={String(data.unconfirmed_order_count).padStart(2, "0")}
          sub="需交易所对账回写"
          tone={walTone}
          delay={180}
        />
      </section>

      {data.alerts.length > 0 ? (
        <section className="alert-rail reveal" aria-label="告警">
          <div className="alert-head">
            <span className="alert-mark">!</span>
            <div>
              <div className="alert-title">告警</div>
              <div className="alert-hint">
                对账差额、出场单连续查询失败与 WAL 未确认订单；差额可在策略行点击
                “标记人工”声明为手工仓位，系统不会自动收编外部仓位。
              </div>
            </div>
          </div>
          <ul className="alert-list">
            {data.alerts.map((alert) => (
              <li key={`${alert.code}-${alert.order_id ?? ""}-${alert.created_at ?? ""}`}>
                <span className={`severity severity-${alert.severity}`}>
                  {alert.severity.toUpperCase()}
                </span>
                <span className="mono alert-order">{alert.code}</span>
                <span className="alert-strategy">{alert.message}</span>
                <span className="mono alert-time">{formatUtc(alert.created_at)}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className="panel reveal" style={{ animationDelay: "220ms" }}>
        <div className="panel-head">
          <div>
            <h2 className="panel-title">策略账本</h2>
            <span className="panel-sub mono">STRATEGY LEDGER · {data.strategies.length} KEYS</span>
          </div>
          <span className="panel-note">按 strategy_key 聚合 · 控制命令异步执行</span>
        </div>

        {data.strategies.length === 0 ? (
          <EmptyState title="账本中还没有策略" hint="策略进程产生第一笔委托后，这里会出现聚合行。" />
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>策略键 / STRATEGY KEY</th>
                  <th>交易对</th>
                  <th>方向</th>
                  <th>运行态</th>
                  <th className="num">活跃</th>
                  <th className="num">归档</th>
                  <th className="num">已实现盈亏</th>
                  <th className="num">挂单</th>
                  <th className="num">未确认</th>
                  <th>控制 / CONTROL</th>
                  <th>最后更新 · UTC</th>
                </tr>
              </thead>
              <tbody>
                {data.strategies.map((strategy, index) => (
                  <tr
                    key={strategy.strategy_key}
                    className="reveal"
                    style={{ animationDelay: `${260 + index * 40}ms` }}
                  >
                    <td>
                      <span className="cell-primary mono">{strategy.strategy_key}</span>
                    </td>
                    <td>
                      <div className="cell-stack">
                        <span className="cell-primary">{strategy.symbol}</span>
                        {strategy.timeframe ? (
                          <span className="cell-sub mono">{strategy.timeframe}</span>
                        ) : null}
                      </div>
                    </td>
                    <td>
                      <Pill variant={strategy.position_side === "long" ? "green" : "red"}>
                        {strategy.position_side === "long" ? "多 LONG" : "空 SHORT"}
                      </Pill>
                    </td>
                    <td>
                      <div className="cell-stack">
                        <RuntimePill alive={strategy.alive} running={strategy.running} />
                        <ReconcileBadge
                          strategy={strategy}
                          onMarkAck={onMarkAck}
                          onRevokeAck={onRevokeAck}
                        />
                      </div>
                    </td>
                    <td className="num mono">{formatNumber(strategy.active_positions)}</td>
                    <td className="num mono dim">{formatNumber(strategy.archived_positions)}</td>
                    <td className="num">
                      <PnL value={strategy.realized_pnl} />
                    </td>
                    <td className="num mono">{formatNumber(strategy.open_orders)}</td>
                    <td className="num">
                      {strategy.unconfirmed_orders > 0 ? (
                        <Pill variant="amber">{strategy.unconfirmed_orders} WAL</Pill>
                      ) : (
                        <span className="mono dim">0</span>
                      )}
                    </td>
                    <td>
                      <div className="row-actions">
                        <button
                          type="button"
                          className="btn-xs"
                          disabled={busy || !strategy.alive}
                          title={
                            strategy.alive
                              ? "停止新的入场/离场决策"
                              : "策略进程未运行, 命令无法执行"
                          }
                          onClick={() => onQuick(strategy.strategy_key, "pause")}
                        >
                          暂停
                        </button>
                        <button
                          type="button"
                          className="btn-xs"
                          disabled={busy || !strategy.alive}
                          title={
                            strategy.alive
                              ? "恢复入场/离场决策"
                              : "策略进程未运行, 命令无法执行"
                          }
                          onClick={() => onQuick(strategy.strategy_key, "resume")}
                        >
                          恢复
                        </button>
                        <button
                          type="button"
                          className="btn-xs btn-xs-danger"
                          disabled={busy || !strategy.alive}
                          title={
                            strategy.alive
                              ? "下一根完成 K 线全平"
                              : "策略进程未运行, 命令无法执行"
                          }
                          onClick={() =>
                            onRequest({
                              kind: "close_all",
                              strategyKey: strategy.strategy_key,
                            })
                          }
                        >
                          全平
                        </button>
                        <button
                          type="button"
                          className="btn-xs btn-xs-amber"
                          disabled={busy || !strategy.alive}
                          title={
                            strategy.alive
                              ? "撤销未成交入场单, 出场单不受影响"
                              : "策略进程未运行, 命令无法执行"
                          }
                          onClick={() =>
                            onRequest({
                              kind: "cancel_entries",
                              strategyKey: strategy.strategy_key,
                            })
                          }
                        >
                          撤入场
                        </button>
                      </div>
                    </td>
                    <td className="mono dim">{formatUtc(strategy.last_updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

function RuntimePill({
  alive,
  running,
}: {
  alive: boolean;
  running: boolean | null;
}) {
  if (!alive) {
    return <Pill variant="dim">离线 OFFLINE</Pill>;
  }
  if (running) {
    return <Pill variant="green">运行中 RUNNING</Pill>;
  }
  return <Pill variant="amber">已暂停 PAUSED</Pill>;
}

function ReconcileBadge({
  strategy,
  onMarkAck,
  onRevokeAck,
}: {
  strategy: StrategySummaryView;
  onMarkAck: (strategyKey: string, surplus: number) => void;
  onRevokeAck: (ack: PositionAckView) => void;
}) {
  const reconcile = strategy.reconcile;
  const ack = strategy.ack;
  const surplus =
    reconcile?.exchange_qty != null && reconcile.local_qty != null
      ? reconcile.exchange_qty - reconcile.local_qty
      : 0;

  if (ack) {
    const stale =
      reconcile?.checked_at != null &&
      reconcile.exchange_qty != null &&
      reconcile.local_qty != null &&
      Math.abs(ack.external_qty - Math.max(surplus, 0)) > 0.000001;
    return (
      <div className="reconcile-actions">
        <Pill variant={stale ? "amber" : "blue"} title={ack.note || undefined}>
          {stale ? "人工基线失效" : `人工 ${formatNumber(ack.external_qty)}`}
        </Pill>
        {stale && surplus > 0.000001 ? (
          <button
            type="button"
            className="btn-xs"
            onClick={() => onMarkAck(strategy.strategy_key, surplus)}
          >
            更新
          </button>
        ) : null}
        <button
          type="button"
          className="btn-xs"
          onClick={() => onRevokeAck(ack)}
        >
          撤销
        </button>
      </div>
    );
  }

  if (reconcile?.checked_at && surplus > 0.000001) {
    return (
      <div className="reconcile-actions">
        <Pill variant="red">差额 {formatNumber(reconcile.gap)}</Pill>
        <button
          type="button"
          className="btn-xs btn-xs-amber"
          onClick={() => onMarkAck(strategy.strategy_key, surplus)}
        >
          标记人工
        </button>
      </div>
    );
  }

  if (reconcile && reconcile.max_exit_query_failures >= 3) {
    return (
      <Pill variant="amber">
        查单失败 {reconcile.max_exit_query_failures}
      </Pill>
    );
  }
  return null;
}

function CommandHistory() {
  const commands = useCommands("", 12);
  const data = commands.data;

  return (
    <section className="panel reveal" style={{ animationDelay: "320ms" }}>
      <div className="panel-head">
        <div>
          <h2 className="panel-title">控制命令</h2>
          <span className="panel-sub mono">
            COMMAND AUDIT · {data ? data.total : 0}
          </span>
        </div>
        <span className="panel-note">每 2s 认领 · 未运行策略进程时保持 PENDING</span>
      </div>

      {commands.isPending ? <LoadingBlock label="读取命令审计" /> : null}
      {commands.isError ? (
        <ErrorPanel
          message={
            commands.error instanceof Error
              ? commands.error.message
              : String(commands.error)
          }
          onRetry={() => {
            void commands.refetch();
          }}
        />
      ) : null}

      {data ? (
        data.items.length === 0 ? (
          <EmptyState
            title="还没有控制命令"
            hint="暂停、恢复、全平、撤入场单与 kill switch 都会记录在这里。"
          />
        ) : (
          <CommandTable data={data} />
        )
      ) : null}
    </section>
  );
}

function CommandTable({ data }: { data: CommandPage }) {
  return (
    <div className="table-scroll">
      <table className="data-table command-table">
        <thead>
          <tr>
            <th>时间 · UTC</th>
            <th>目标 / TARGET</th>
            <th>动作</th>
            <th>状态</th>
            <th>结果 / RESULT</th>
            <th>发起</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((command) => (
            <CommandRow key={command.id} command={command} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CommandRow({ command }: { command: CommandView }) {
  return (
    <tr className={command.status === "failed" ? "is-failed" : ""}>
      <td className="mono dim">{formatUtc(command.requested_at)}</td>
      <td className="mono cell-primary">{command.strategy_key ?? "全局 / ALL"}</td>
      <td>
        <span className="mono">{actionShortLabel[command.action]}</span>
      </td>
      <td>
        <Pill variant={commandStatusVariant[command.status]}>
          {commandStatusLabel[command.status]}
        </Pill>
      </td>
      <td className="result-cell" title={command.result ?? undefined}>
        {command.result ?? "—"}
      </td>
      <td className="mono dim">{command.requested_by}</td>
    </tr>
  );
}
