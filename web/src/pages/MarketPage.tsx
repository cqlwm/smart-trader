import { useEffect, useRef, useState } from "react";

import { useMarketChart, useOrders, usePositions, useStrategies } from "../api/hooks";
import type { MarketDirection } from "../api/types";
import { MarketChart } from "../components/MarketChart";
import {
  EmptyState,
  ErrorPanel,
  LoadingBlock,
  PageHeader,
  Pill,
  RefreshButton,
} from "../components/ui";
import { formatNumber, formatUtc } from "../utils/format";

const TIMEFRAMES = ["1m", "5m", "15m", "1h"] as const;

const directionLabel: Record<MarketDirection, string> = {
  long: "多头 LONG",
  short: "空头 SHORT",
  neutral: "中性 NEUTRAL",
};

const directionVariant: Record<
  MarketDirection,
  "green" | "red" | "dim"
> = {
  long: "green",
  short: "red",
  neutral: "dim",
};

export function MarketPage() {
  const strategies = useStrategies();
  const [strategyKey, setStrategyKey] = useState("");
  const [symbol, setSymbol] = useState("DOGE/USDT");
  const [timeframe, setTimeframe] = useState<string>("1m");
  const initialized = useRef(false);

  useEffect(() => {
    if (initialized.current || !strategies.data) {
      return;
    }
    const preferred =
      strategies.data.find((strategy) => strategy.alive) ?? strategies.data[0];
    if (preferred) {
      setStrategyKey(preferred.strategy_key);
      setSymbol(preferred.symbol);
      setTimeframe(preferred.timeframe ?? "1m");
    }
    initialized.current = true;
  }, [strategies.data]);

  const chart = useMarketChart(symbol, timeframe, 300);
  const orders = useOrders(strategyKey, "", 200, 0);
  const positions = usePositions(strategyKey, false);

  const symbols = Array.from(
    new Set([
      symbol,
      ...(strategies.data ?? []).map((strategy) => strategy.symbol),
    ]),
  );

  function selectStrategy(key: string) {
    setStrategyKey(key);
    const selected = strategies.data?.find(
      (strategy) => strategy.strategy_key === key,
    );
    if (selected) {
      setSymbol(selected.symbol);
      if (selected.timeframe) {
        setTimeframe(selected.timeframe);
      }
    }
  }

  const data = chart.data;
  const activePositions = (positions.data ?? []).filter(
    (position) => position.active && position.symbol === symbol,
  );

  return (
    <>
      <PageHeader
        kicker="03 / MARKET"
        title="行情"
        description="Binance 公共 K 线与 AlphaTrend 指标；叠加当前仓位的入场/止损/止盈价格线与订单成交标记。"
        meta={
          data
            ? `${data.symbol} · ${data.timeframe} · ${formatUtc(data.fetched_at)} UTC`
            : undefined
        }
        actions={
          <RefreshButton
            onClick={() => {
              void chart.refetch();
            }}
            busy={chart.isFetching}
          />
        }
      />

      <section className="toolbar reveal" aria-label="行情筛选">
        <label className="field">
          <span>策略</span>
          <select
            value={strategyKey}
            onChange={(event) => selectStrategy(event.target.value)}
          >
            <option value="">全部 / 手动</option>
            {(strategies.data ?? []).map((strategy) => (
              <option key={strategy.strategy_key} value={strategy.strategy_key}>
                {strategy.strategy_key}
                {strategy.alive ? " · LIVE" : ""}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>交易对</span>
          <select
            value={symbol}
            onChange={(event) => setSymbol(event.target.value)}
          >
            {symbols.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>周期</span>
          <select
            value={timeframe}
            onChange={(event) => setTimeframe(event.target.value)}
          >
            {TIMEFRAMES.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>

        <span className="toolbar-note mono">
          ALPHA TREND · ATR 1.0 × PERIOD 8 · 10s REFRESH
        </span>
      </section>

      {chart.isPending ? <LoadingBlock label="拉取公共行情" /> : null}
      {chart.isError ? (
        <ErrorPanel
          message={
            chart.error instanceof Error
              ? chart.error.message
              : String(chart.error)
          }
          onRetry={() => {
            void chart.refetch();
          }}
        />
      ) : null}

      {data ? (
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
              <Pill variant={directionVariant[data.direction]}>
                {directionLabel[data.direction]}
              </Pill>
            </div>
            <div className="market-stat">
              <span className="market-stat-label">累积计数</span>
              <span className="market-stat-value mono">{data.trend}</span>
            </div>
            <div className="market-stat">
              <span className="market-stat-label">最近突破 / BREAKOUT</span>
              <span className="market-stat-value mono">
                {data.last_breakout_timestamp
                  ? formatUtc(
                      new Date(data.last_breakout_timestamp).toISOString(),
                    )
                  : "—"}
              </span>
            </div>
            <div className="market-stat">
              <span className="market-stat-label">K 线 / CANDLES</span>
              <span className="market-stat-value mono">
                {data.candles.length}
              </span>
            </div>
          </section>

          <section className="panel reveal" style={{ animationDelay: "120ms" }}>
            <div className="panel-head chart-head">
              <div>
                <h2 className="panel-title">K 线 / AlphaTrend</h2>
                <span className="panel-sub mono">
                  {data.symbol} · {data.timeframe} · UTC
                </span>
              </div>
              <div className="chart-legend mono">
                <span>
                  <i className="legend-line legend-alpha" />
                  AlphaTrend
                </span>
                <span>
                  <i className="legend-line legend-entry" />
                  入场线
                </span>
                <span>
                  <i className="legend-line legend-stop" />
                  止损线
                </span>
                <span>
                  <i className="legend-line legend-take" />
                  止盈线
                </span>
                <span>
                  <i className="legend-dot legend-buy" /> 买 / 入场
                </span>
                <span>
                  <i className="legend-dot legend-sell" /> 卖 / 出场
                </span>
              </div>
            </div>
            <div className="chart-wrap">
              <MarketChart
                data={data}
                orders={orders.data?.items ?? []}
                positions={activePositions}
                timeframe={data.timeframe}
              />
            </div>
          </section>

          <section
            className="panel reveal market-positions"
            style={{ animationDelay: "180ms" }}
          >
            <div className="panel-head">
              <div>
                <h2 className="panel-title">仓位价格线</h2>
                <span className="panel-sub mono">
                  ACTIVE POSITIONS · {activePositions.length}
                </span>
              </div>
              <span className="panel-note">
                仅显示当前交易对；最多叠加 10 层
              </span>
            </div>
            {activePositions.length === 0 ? (
              <EmptyState
                title="当前交易对没有活跃仓位"
                hint="策略建仓后，入场/止损/止盈线会自动叠加到图上。"
              />
            ) : (
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>仓位 / POSITION</th>
                      <th>方向</th>
                      <th className="num">数量</th>
                      <th className="num">入场</th>
                      <th className="num">止损</th>
                      <th className="num">止盈</th>
                      <th className="num">已实现盈亏</th>
                      <th>相位</th>
                    </tr>
                  </thead>
                  <tbody>
                    {activePositions.map((position) => (
                      <tr key={position.id}>
                        <td className="mono">{position.id}</td>
                        <td>
                          <Pill
                            variant={
                              position.position_side === "long" ? "green" : "red"
                            }
                          >
                            {position.position_side === "long"
                              ? "多 LONG"
                              : "空 SHORT"}
                          </Pill>
                        </td>
                        <td className="num mono">
                          {formatNumber(position.quantity)}
                        </td>
                        <td className="num mono">
                          {formatNumber(position.price)}
                        </td>
                        <td className="num mono">
                          {formatNumber(position.stop_loss_price)}
                        </td>
                        <td className="num mono">
                          {formatNumber(position.take_profit_price)}
                        </td>
                        <td className="num mono">
                          {formatNumber(position.realized_pnl)}
                        </td>
                        <td className="mono dim">{position.phase}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      ) : null}

      <p className="foot-note reveal">
        行情来自 Binance 公共接口（零凭据），指标复用策略层 AlphaTrend 实现；API 只读公共行情，
        不触达交易与账户。标记按 K 线周期归并，WAL 未确认订单以琥珀色显示。
      </p>
    </>
  );
}
