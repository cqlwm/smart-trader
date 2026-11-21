import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router-dom";

import { newIdempotencyKey } from "../api/client";
import {
  useArchivedPositions,
  useConfig,
  useDisableStrategy,
  useEnableStrategy,
  useMarketChart,
  useOrders,
  usePositions,
  useStrategies,
  useSubmitCommand,
  useUpdateConfig,
} from "../api/hooks";
import type {
  DisablePositionPolicy,
  StrategyConfigPayload,
  StrategyConfigView,
} from "../api/types";
import {
  ConfigEditor,
  draftFromPayload,
  payloadFromDraft,
} from "../components/ConfigEditor";
import type { Draft, EditorState } from "../components/ConfigEditor";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { MarketChart } from "../components/MarketChart";
import { OrderTable } from "../components/OrderTable";
import { PositionTable } from "../components/PositionTable";
import {
  EmptyState,
  ErrorPanel,
  LoadingBlock,
  PageHeader,
  Pill,
} from "../components/ui";
import { formatNumber, formatUtc } from "../utils/format";

type Tab = "config" | "positions" | "archived" | "orders" | "market";

const TABS: { value: Tab; label: string }[] = [
  { value: "config", label: "配置" },
  { value: "positions", label: "持仓" },
  { value: "archived", label: "归档" },
  { value: "orders", label: "订单" },
  { value: "market", label: "行情" },
];

const ARCHIVE_PAGE_SIZE = 20;

const DISABLE_POLICY_OPTIONS: {
  value: DisablePositionPolicy;
  label: string;
  hint: string;
}[] = [
  {
    value: "close_all",
    label: "全平后停用",
    hint: "先挂出场单并等待仓位清零，再完成停用；可能等待较久。",
  },
  {
    value: "limit_exit",
    label: "挂限价单后停用",
    hint: "先挂出场单，随后进入守护态；不等待成交。",
  },
  {
    value: "keep",
    label: "冻结保留",
    hint: "不平仓、不挂新单；进入守护态继续跟踪已有挂单与对账，直到重新启用。",
  },
  {
    value: "abandon",
    label: "抛弃不管",
    hint: "本地归档仓位并停止管理；交易所持仓与挂单不会处理，需人工接管。",
  },
];

const DISABLE_POLICY_LABELS: Record<DisablePositionPolicy, string> = {
  close_all: "全平",
  limit_exit: "挂出场单",
  keep: "冻结保留",
  abandon: "抛弃",
};

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function StrategyDetailPage() {
  const params = useParams<{ strategyKey: string }>();
  const strategyKey = params.strategyKey ?? "";

  const config = useConfig(strategyKey);
  const strategies = useStrategies();
  const submitCommand = useSubmitCommand();
  const disableStrategy = useDisableStrategy();
  const enableStrategy = useEnableStrategy();
  const positions = usePositions(strategyKey, false);
  const [tab, setTab] = useState<Tab>("config");
  const [archiveOffset, setArchiveOffset] = useState(0);
  const archived = useArchivedPositions(
    strategyKey,
    ARCHIVE_PAGE_SIZE,
    archiveOffset,
    tab === "archived",
  );
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [disableOpen, setDisableOpen] = useState(false);
  const [disablePolicy, setDisablePolicy] =
    useState<DisablePositionPolicy>("close_all");
  const [disableError, setDisableError] = useState<string | null>(null);

  useEffect(() => {
    setTab("config");
    setArchiveOffset(0);
    setNotice(null);
    setActionError(null);
    setDisableOpen(false);
    setDisableError(null);
  }, [strategyKey]);

  const summary = strategies.data?.find(
    (item) => item.strategy_key === strategyKey,
  );
  const record = config.data;

  if (!strategyKey) {
    return <EmptyState title="缺少策略键" hint="请从策略列表进入详情。" />;
  }
  if (config.isPending) {
    return <LoadingBlock label="读取策略详情中" />;
  }
  if (config.isError || !record) {
    return (
      <ErrorPanel
        message={messageOf(config.error)}
        onRetry={() => {
          void config.refetch();
        }}
      />
    );
  }

  async function togglePause() {
    setNotice(null);
    setActionError(null);
    const action = summary?.running === false ? "resume" : "pause";
    try {
      const command = await submitCommand.mutateAsync({
        strategyKey,
        action,
        idempotencyKey: newIdempotencyKey(),
      });
      setNotice(
        `命令 ${command.id}（${action === "pause" ? "暂停" : "恢复"}）已提交，等待策略进程执行。`,
      );
    } catch (error) {
      setActionError(messageOf(error));
    }
  }

  async function handleDisable() {
    if (!record) {
      return;
    }
    setDisableError(null);
    try {
      await disableStrategy.mutateAsync({
        strategyKey,
        policy: disablePolicy,
        expectedVersion: record.version,
      });
      setDisableOpen(false);
      setNotice("停用请求已提交；策略已暂停，持仓处置进行中。");
      setActionError(null);
    } catch (error) {
      setDisableError(messageOf(error));
    }
  }

  async function handleEnable() {
    if (!record) {
      return;
    }
    setNotice(null);
    setActionError(null);
    try {
      await enableStrategy.mutateAsync({
        strategyKey,
        expectedVersion: record.version,
      });
      setNotice("已启用；未运行时会自动热加载。");
    } catch (error) {
      setActionError(messageOf(error));
    }
  }

  const paused = summary?.running === false;
  const pausedUnknown = summary === undefined || summary.running === null;
  const canPause = summary?.alive === true;
  const lifecycleActive = record.lifecycle === "active" && record.enabled;
  const lifecycleLabel =
    record.lifecycle === "disabling"
      ? "停用中"
      : record.lifecycle === "guard"
        ? "守护中"
        : record.lifecycle === "disabled"
          ? "已停用"
          : record.desired_running
            ? "启用"
            : "已暂停";
  const lifecycleVariant =
    record.lifecycle === "disabling"
      ? "amber"
      : record.lifecycle === "guard"
        ? "blue"
        : record.lifecycle === "disabled"
          ? "dim"
          : "green";
  const disableHint =
    DISABLE_POLICY_OPTIONS.find((item) => item.value === disablePolicy)?.hint ??
    "";

  return (
    <>
      <div className="reveal">
        <Link className="mono dim" to="/strategies">
          ← 返回策略列表
        </Link>
      </div>

      <PageHeader
        kicker="02 / STRATEGY"
        title={record.strategy_key}
        description={`${record.payload.symbol} · ${record.payload.timeframe} · ${record.payload.position_side.toUpperCase()} · ${record.payload.data_source.toUpperCase()}`}
        meta={`v${record.version} · ${formatUtc(record.updated_at)} UTC`}
        actions={
          <div className="config-head-actions">
            {lifecycleActive ? (
              <>
                <button
                  type="button"
                  className="button"
                  onClick={() => {
                    void togglePause();
                  }}
                  disabled={
                    !canPause || submitCommand.isPending || pausedUnknown
                  }
                  title={
                    canPause
                      ? paused
                        ? "恢复入场/离场决策"
                        : "停止新的入场/离场决策"
                      : "策略进程未运行, 命令无法执行"
                  }
                >
                  {paused ? "恢复" : "暂停"}
                </button>
                <button
                  type="button"
                  className="button button-danger"
                  onClick={() => {
                    setDisableError(null);
                    setDisableOpen(true);
                  }}
                >
                  停用
                </button>
              </>
            ) : (
              <button
                type="button"
                className="button button-amber"
                onClick={() => {
                  void handleEnable();
                }}
                disabled={enableStrategy.isPending}
              >
                {record.lifecycle === "disabling"
                  ? "取消停用并启用"
                  : "启用"}
              </button>
            )}
          </div>
        }
      />

      {notice ? <div className="config-notice reveal">{notice}</div> : null}
      {actionError ? (
        <div className="config-error config-error-block reveal">
          {actionError}
        </div>
      ) : null}

      <section className="market-strip reveal" aria-label="策略运行状态">
        <StatusItem label="配置 / CONFIG">
          <Pill variant={lifecycleVariant}>{lifecycleLabel}</Pill>
        </StatusItem>
        {record.disable_policy ? (
          <StatusItem label="停用策略 / POLICY">
            <span className="market-stat-value mono">
              {DISABLE_POLICY_LABELS[record.disable_policy]}
            </span>
          </StatusItem>
        ) : null}
        <StatusItem label="进程 / PROCESS">
          <Pill variant={summary?.alive ? "green" : "dim"}>
            {summary?.alive ? "在线" : "离线"}
          </Pill>
        </StatusItem>
        <StatusItem label="决策 / DECISION">
          <Pill
            variant={
              pausedUnknown ? "dim" : paused ? "amber" : "green"
            }
          >
            {pausedUnknown ? "—" : paused ? "已暂停" : "运行中"}
          </Pill>
        </StatusItem>
        <StatusItem label="活跃仓位 / 挂单">
          <span className="market-stat-value mono">
            {summary
              ? `${summary.active_positions} / ${summary.open_orders}`
              : "—"}
          </span>
        </StatusItem>
        <StatusItem label="对账差额 / GAP">
          <span className="market-stat-value mono">
            {summary?.reconcile
              ? formatNumber(summary.reconcile.gap)
              : "—"}
          </span>
        </StatusItem>
        <StatusItem label="心跳 / HEARTBEAT">
          <span className="market-stat-value mono">
            {summary?.heartbeat_at ? formatUtc(summary.heartbeat_at) : "—"}
          </span>
        </StatusItem>
      </section>

      <section className="toolbar reveal" aria-label="策略详情导航">
        <div className="segmented">
          {TABS.map((item) => (
            <button
              key={item.value}
              type="button"
              className={tab === item.value ? "is-active" : ""}
              onClick={() => setTab(item.value)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <span className="toolbar-note mono">
          {record.payload.symbol} · {record.payload.timeframe}
        </span>
      </section>

      {tab === "config" ? (
        <ConfigPanel
          key={`${record.strategy_key}-v${record.version}`}
          config={record}
          onSaved={(message) => {
            setNotice(message);
            setActionError(null);
          }}
        />
      ) : null}

      {tab === "positions" ? (
        <>
          {positions.isPending ? <LoadingBlock label="读取活跃仓位" /> : null}
          {positions.isError ? (
            <ErrorPanel
              message={messageOf(positions.error)}
              onRetry={() => {
                void positions.refetch();
              }}
            />
          ) : null}
          {positions.data ? (
            <PositionTable
              positions={positions.data}
              emptyTitle="没有活跃仓位"
              emptyHint="策略建仓后会在这里出现。"
            />
          ) : null}
        </>
      ) : null}

      {tab === "archived" ? (
        <>
          {archived.isPending ? <LoadingBlock label="读取归档仓位" /> : null}
          {archived.isError ? (
            <ErrorPanel
              message={messageOf(archived.error)}
              onRetry={() => {
                void archived.refetch();
              }}
            />
          ) : null}
          {archived.data ? (
            <>
              <PositionTable
                positions={archived.data.items}
                emptyTitle="没有归档仓位"
                emptyHint="仓位归档后保留原订单行与归档原因，供审计查询。"
              />
              <div className="pagination">
                <span className="mono dim">
                  OFFSET {archiveOffset} · LIMIT {ARCHIVE_PAGE_SIZE}
                </span>
                <div className="pagination-controls">
                  <button
                    type="button"
                    className="button"
                    disabled={archiveOffset === 0}
                    onClick={() =>
                      setArchiveOffset(
                        Math.max(0, archiveOffset - ARCHIVE_PAGE_SIZE),
                      )
                    }
                  >
                    上一页
                  </button>
                  <span className="mono">
                    {Math.floor(archiveOffset / ARCHIVE_PAGE_SIZE) + 1} /{" "}
                    {Math.max(
                      1,
                      Math.ceil(archived.data.total / ARCHIVE_PAGE_SIZE),
                    )}
                  </span>
                  <button
                    type="button"
                    className="button"
                    disabled={
                      archiveOffset + ARCHIVE_PAGE_SIZE >= archived.data.total
                    }
                    onClick={() =>
                      setArchiveOffset(archiveOffset + ARCHIVE_PAGE_SIZE)
                    }
                  >
                    下一页
                  </button>
                </div>
                <span className="mono dim">{archived.data.total} ROWS</span>
              </div>
            </>
          ) : null}
        </>
      ) : null}

      {tab === "orders" ? (
        <OrderTable key={strategyKey} strategyKey={strategyKey} />
      ) : null}

      {tab === "market" ? (
        <MarketPanel
          strategyKey={strategyKey}
          symbol={record.payload.symbol}
          timeframe={record.payload.timeframe}
        />
      ) : null}

      <ConfirmDialog
        open={disableOpen}
        kicker="LIFECYCLE / DISABLE"
        title={`停用 ${record.strategy_key}？`}
        description="停用会立即暂停策略，并按所选方式处置已有持仓；全平模式可能等待较长时间。"
        confirmLabel="确认停用"
        tone="danger"
        busy={disableStrategy.isPending}
        error={disableError}
        onClose={() => {
          if (!disableStrategy.isPending) {
            setDisableOpen(false);
            setDisableError(null);
          }
        }}
        onConfirm={() => {
          void handleDisable();
        }}
      >
        <label className="modal-field">
          <span>持仓处置 / POSITION POLICY</span>
          <select
            value={disablePolicy}
            onChange={(event) =>
              setDisablePolicy(event.target.value as DisablePositionPolicy)
            }
          >
            {DISABLE_POLICY_OPTIONS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <p className="modal-desc">{disableHint}</p>
      </ConfirmDialog>
    </>
  );
}

function StatusItem({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="market-stat">
      <span className="market-stat-label">{label}</span>
      {children}
    </div>
  );
}

function ConfigPanel({
  config,
  onSaved,
}: {
  config: StrategyConfigView;
  onSaved: (message: string) => void;
}) {
  const updateConfig = useUpdateConfig();
  const [editor, setEditor] = useState<EditorState>(() =>
    makeEditorState(config),
  );
  const [formError, setFormError] = useState<string | null>(null);

  function patch<K extends keyof Draft>(key: K, value: Draft[K]) {
    setEditor((current) => ({
      ...current,
      draft: { ...current.draft, [key]: value },
    }));
  }

  async function save() {
    setFormError(null);
    let payload: StrategyConfigPayload;
    try {
      payload = payloadFromDraft(editor.draft);
    } catch (error) {
      setFormError(messageOf(error));
      return;
    }
    try {
      const updated = await updateConfig.mutateAsync({
        strategyKey: config.strategy_key,
        body: {
          expected_version: editor.version,
          payload,
        },
      });
      setEditor(makeEditorState(updated));
      onSaved("配置已保存；调参将自动热更新（身份字段仍需重启）。");
    } catch (error) {
      setFormError(messageOf(error));
    }
  }

  return (
    <ConfigEditor
      editor={editor}
      formError={formError}
      saving={updateConfig.isPending}
      onPatch={patch}
      onCancel={() => {
        setEditor(makeEditorState(config));
        setFormError(null);
      }}
      onSave={() => {
        void save();
      }}
      showEnabled={false}
    />
  );
}

function makeEditorState(config: StrategyConfigView): EditorState {
  return {
    mode: "edit",
    version: config.version,
    draft: draftFromPayload(config.payload, config.strategy_key, config.enabled),
  };
}

function MarketPanel({
  strategyKey,
  symbol,
  timeframe,
}: {
  strategyKey: string;
  symbol: string;
  timeframe: string;
}) {
  const chart = useMarketChart(symbol, timeframe, 300);
  const orders = useOrders(strategyKey, "", 200, 0);
  const positions = usePositions(strategyKey, false);

  if (chart.isPending) {
    return <LoadingBlock label="拉取公共行情" />;
  }
  if (chart.isError) {
    return (
      <ErrorPanel
        message={messageOf(chart.error)}
        onRetry={() => {
          void chart.refetch();
        }}
      />
    );
  }
  const data = chart.data;
  if (!data) {
    return null;
  }

  return (
    <>
      <section className="market-strip reveal">
        <div className="market-stat">
          <span className="market-stat-label">最新价 / LAST</span>
          <span className="market-stat-value">
            {formatNumber(data.last_price)}
          </span>
        </div>
        <div className="market-stat">
          <span className="market-stat-label">趋势 / TREND</span>
          <span className="market-stat-value mono">{data.trend}</span>
        </div>
        <div className="market-stat">
          <span className="market-stat-label">K 线 / CANDLES</span>
          <span className="market-stat-value mono">{data.candles.length}</span>
        </div>
        <div className="market-stat">
          <span className="market-stat-label">刷新 / FETCHED</span>
          <span className="market-stat-value mono">
            {formatUtc(data.fetched_at)}
          </span>
        </div>
      </section>

      <section className="panel reveal">
        <div className="panel-head chart-head">
          <div>
            <h2 className="panel-title">K 线 / AlphaTrend</h2>
            <span className="panel-sub mono">
              {data.symbol} · {data.timeframe} · UTC
            </span>
          </div>
          <span className="panel-note">
            当前策略订单与仓位价格线已叠加
          </span>
        </div>
        <div className="chart-wrap">
          <MarketChart
            data={data}
            orders={orders.data?.items ?? []}
            positions={positions.data ?? []}
            timeframe={data.timeframe}
          />
        </div>
      </section>
    </>
  );
}
