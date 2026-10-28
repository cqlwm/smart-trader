import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
} from "lightweight-charts";
import type {
  CandlestickData,
  IChartApi,
  IPriceLine,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  LineData,
  SeriesMarker,
  Time,
  UTCTimestamp,
} from "lightweight-charts";

import type {
  MarketChartView,
  OrderView,
  PositionView,
} from "../api/types";

const TIMEFRAME_SECONDS: Record<string, number> = {
  "1m": 60,
  "5m": 300,
  "15m": 900,
  "1h": 3600,
};

function bucketSeconds(timeframe: string): number {
  return TIMEFRAME_SECONDS[timeframe] ?? 60;
}

function toSeconds(timestamp: number): UTCTimestamp {
  return Math.floor(timestamp / 1000) as UTCTimestamp;
}

function pricePrecision(lastPrice: number): number {
  if (!Number.isFinite(lastPrice) || lastPrice <= 0) {
    return 2;
  }
  return Math.max(2, Math.min(8, Math.ceil(-Math.log10(lastPrice)) + 4));
}

function formatAxisTime(time: Time): string {
  if (typeof time !== "number") {
    return String(time);
  }
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "UTC",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(time * 1000));
}

function formatTooltipTime(time: Time): string {
  if (typeof time !== "number") {
    return String(time);
  }
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "UTC",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(time * 1000));
}

export function MarketChart({
  data,
  orders,
  positions,
  timeframe,
}: {
  data: MarketChartView;
  orders: OrderView[];
  positions: PositionView[];
  timeframe: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const alphaRef = useRef<ISeriesApi<"Line"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }

    const chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#0b0c0b" },
        textColor: "#9b968a",
        fontFamily: '"IBM Plex Mono", "SFMono-Regular", monospace',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "rgba(233, 228, 216, 0.045)" },
        horzLines: { color: "rgba(233, 228, 216, 0.045)" },
      },
      rightPriceScale: {
        borderColor: "rgba(233, 228, 216, 0.12)",
        scaleMargins: { top: 0.1, bottom: 0.12 },
      },
      timeScale: {
        borderColor: "rgba(233, 228, 216, 0.12)",
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: Time) => formatAxisTime(time),
      },
      localization: {
        timeFormatter: (time: Time) => formatTooltipTime(time),
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: "rgba(226, 164, 75, 0.45)",
          labelBackgroundColor: "#e2a44b",
        },
        horzLine: {
          color: "rgba(226, 164, 75, 0.45)",
          labelBackgroundColor: "#e2a44b",
        },
      },
    });

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "#77c39a",
      downColor: "#d9705f",
      borderVisible: false,
      wickUpColor: "#77c39a",
      wickDownColor: "#d9705f",
      priceLineVisible: false,
      lastValueVisible: true,
    });
    const alpha = chart.addSeries(LineSeries, {
      color: "#e2a44b",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    chartRef.current = chart;
    candleRef.current = candles;
    alphaRef.current = alpha;
    markersRef.current = createSeriesMarkers(candles, []);

    return () => {
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      alphaRef.current = null;
      markersRef.current = null;
      priceLinesRef.current = [];
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    const candles = candleRef.current;
    const alpha = alphaRef.current;
    if (!chart || !candles || !alpha) {
      return;
    }

    const visible = chart.timeScale().getVisibleLogicalRange();
    const precision = pricePrecision(data.last_price);
    const priceFormat = {
      type: "price" as const,
      precision,
      minMove: 10 ** -precision,
    };
    candles.applyOptions({ priceFormat });
    alpha.applyOptions({ priceFormat });
    candles.setData(
      data.candles.map(
        (candle): CandlestickData<Time> => ({
          time: toSeconds(candle.timestamp),
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
        }),
      ),
    );
    alpha.setData(
      data.indicator
        .filter((point) => point.alpha_value !== null)
        .map(
          (point): LineData<Time> => ({
            time: toSeconds(point.timestamp),
            value: point.alpha_value as number,
          }),
        ),
    );
    if (visible) {
      chart.timeScale().setVisibleLogicalRange(visible);
    } else {
      chart.timeScale().fitContent();
    }
  }, [data]);

  useEffect(() => {
    const markers = markersRef.current;
    if (!markers) {
      return;
    }

    const candleTimes = new Set(
      data.candles.map((candle) => toSeconds(candle.timestamp)),
    );
    const bucket = bucketSeconds(timeframe);
    const items: SeriesMarker<Time>[] = [];
    for (const order of orders) {
      if (order.symbol !== data.symbol) {
        continue;
      }
      const milliseconds = Date.parse(order.created_at);
      if (!Number.isFinite(milliseconds)) {
        continue;
      }
      const time = (Math.floor(milliseconds / 1000 / bucket) *
        bucket) as UTCTimestamp;
      if (!candleTimes.has(time)) {
        continue;
      }
      const isBuy = order.side === "buy";
      items.push({
        time,
        position: isBuy ? "belowBar" : "aboveBar",
        color: order.unconfirmed
          ? "#e2a44b"
          : isBuy
            ? "#77c39a"
            : "#d9705f",
        shape: isBuy ? "arrowUp" : "arrowDown",
        text: order.role === "entry" ? "IN" : "OUT",
      });
    }
    markers.setMarkers(items.slice(-40));
  }, [orders, data, timeframe]);

  useEffect(() => {
    const series = candleRef.current;
    if (!series) {
      return;
    }

    for (const line of priceLinesRef.current) {
      series.removePriceLine(line);
    }
    priceLinesRef.current = [];

    const active = positions
      .filter((position) => position.active && position.symbol === data.symbol)
      .slice(0, 10);
    for (const position of active) {
      const tag = position.id.slice(-4);
      priceLinesRef.current.push(
        series.createPriceLine({
          price: position.price,
          color: "#7fa6c9",
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: `#${tag} 入场`,
        }),
      );
      if (position.stop_loss_price !== null) {
        priceLinesRef.current.push(
          series.createPriceLine({
            price: position.stop_loss_price,
            color: "#d9705f",
            lineWidth: 1,
            lineStyle: LineStyle.Solid,
            axisLabelVisible: true,
            title: `#${tag} 止损`,
          }),
        );
      }
      if (position.take_profit_price !== null) {
        priceLinesRef.current.push(
          series.createPriceLine({
            price: position.take_profit_price,
            color: "#77c39a",
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title: `#${tag} 止盈`,
          }),
        );
      }
    }
  }, [positions, data.symbol]);

  return <div className="chart-box" ref={containerRef} />;
}
