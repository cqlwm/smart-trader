import { useState } from "react";
import { Link } from "react-router-dom";

import {
  useConfigDefaults,
  useConfigs,
  useCreateConfig,
  useStrategies,
} from "../api/hooks";
import type {
  StrategyConfigPayload,
  StrategyLifecycle,
  StrategySummaryView,
} from "../api/types";
import {
  ConfigEditor,
  draftFromPayload,
  payloadFromDraft,
} from "../components/ConfigEditor";
import type { Draft, EditorState } from "../components/ConfigEditor";
import {
  EmptyState,
  ErrorPanel,
  LoadingBlock,
  PageHeader,
  Pill,
  RefreshButton,
} from "../components/ui";
import { formatUtc } from "../utils/format";

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function StrategiesPage() {
  const configs = useConfigs();
  const strategies = useStrategies();
  const defaults = useConfigDefaults(true);
  const createConfig = useCreateConfig();

  const [editor, setEditor] = useState<EditorState | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const rows = configs.data ?? [];
  const summaries = new Map<string, StrategySummaryView>(
    (strategies.data ?? []).map((item) => [item.strategy_key, item]),
  );

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
    setEditor({
      mode: "create",
      version: 0,
      draft: draftFromPayload(base, "", true),
    });
  }

  async function handleCreate() {
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
    const key = editor.draft.strategy_key.trim();
    if (!key) {
      setFormError("策略键不能为空");
      return;
    }
    try {
      await createConfig.mutateAsync({
        strategy_key: key,
        enabled: editor.draft.enabled,
        payload,
      });
      setNotice(`${key} 已创建；启用后将自动热加载（结构字段与停用仍需重启）。`);
      setEditor(null);
    } catch (error) {
      setFormError(messageOf(error));
    }
  }

  return (
    <>
      <PageHeader
        kicker="02 / STRATEGIES"
        title="策略"
        description="每个策略实例的配置、仓位、归档、订单与行情集中在详情页；运行状态与启停也在详情页处理。"
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
                void strategies.refetch();
              }}
              busy={configs.isFetching || strategies.isFetching}
            />
          </div>
        }
      />

      {notice ? <div className="config-notice reveal">{notice}</div> : null}

      {editor ? (
        <ConfigEditor
          editor={editor}
          formError={formError}
          saving={createConfig.isPending}
          onPatch={patch}
          onCancel={() => {
            setEditor(null);
            setFormError(null);
          }}
          onSave={() => {
            void handleCreate();
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
                  STRATEGIES · {rows.length} ROWS
                </span>
              </div>
              <span className="panel-note">点击策略键进入详情 · 停用代替删除</span>
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
                    <th>配置</th>
                    <th>运行</th>
                    <th className="num">仓位 / 挂单</th>
                    <th className="num">版本</th>
                    <th>更新 · UTC</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((config, index) => {
                    const summary = summaries.get(config.strategy_key);
                    return (
                      <tr
                        key={config.strategy_key}
                        className="reveal"
                        style={{ animationDelay: `${index * 25}ms` }}
                      >
                        <td>
                          <Link
                            className="mono cell-primary"
                            to={`/strategies/${encodeURIComponent(config.strategy_key)}`}
                          >
                            {config.strategy_key}
                          </Link>
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
                              config.payload.data_source === "ws"
                                ? "blue"
                                : "amber"
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
                        <td>
                          <RuntimePill
                            summary={summary}
                            lifecycle={config.lifecycle}
                          />
                        </td>
                        <td className="num mono">
                          {summary
                            ? `${summary.active_positions} / ${summary.open_orders}`
                            : "—"}
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
                          <Link
                            className="button"
                            to={`/strategies/${encodeURIComponent(config.strategy_key)}`}
                          >
                            详情
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        )
      ) : null}

      <p className="foot-note reveal">
        身份字段创建后不可修改；其余参数在详情页保存后自动热更新。
        停用需重启策略进程生效，存在活跃仓位时禁止停用。
      </p>
    </>
  );
}

function RuntimePill({
  summary,
  lifecycle,
}: {
  summary: StrategySummaryView | undefined;
  lifecycle: StrategyLifecycle;
}) {
  if (lifecycle === "disabling") {
    return <Pill variant="amber">停用中</Pill>;
  }
  if (lifecycle === "guard") {
    return <Pill variant="blue">守护中</Pill>;
  }
  if (lifecycle === "disabled") {
    return <Pill variant="dim">已停用</Pill>;
  }
  if (!summary) {
    return <Pill variant="dim">未运行</Pill>;
  }
  if (!summary.alive) {
    return <Pill variant="dim">离线</Pill>;
  }
  if (summary.running === false) {
    return <Pill variant="amber">已暂停</Pill>;
  }
  return <Pill variant="green">运行中</Pill>;
}
