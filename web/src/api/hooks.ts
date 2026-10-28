import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPost, apiPut } from "./client";
import type {
  CommandAction,
  CommandPage,
  CommandView,
  HealthView,
  MarketChartView,
  OrderPage,
  OrderStatus,
  OverviewView,
  PositionDetailView,
  PositionView,
  StrategyConfigCreateRequest,
  StrategyConfigPayload,
  StrategyConfigUpdateRequest,
  StrategyConfigView,
  StrategySummaryView,
} from "./types";

const REFRESH_MS = 10_000;

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: () => apiGet<HealthView>("/api/health"),
    refetchInterval: REFRESH_MS,
  });
}

export function useOverview() {
  return useQuery({
    queryKey: ["overview"],
    queryFn: () => apiGet<OverviewView>("/api/overview"),
    refetchInterval: REFRESH_MS,
  });
}

export function useStrategies() {
  return useQuery({
    queryKey: ["strategies"],
    queryFn: () => apiGet<StrategySummaryView[]>("/api/strategies"),
    refetchInterval: REFRESH_MS,
  });
}

export function usePositions(strategyKey: string, includeClosed: boolean) {
  const params = new URLSearchParams();
  if (strategyKey) {
    params.set("strategy_key", strategyKey);
  }
  params.set("include_closed", String(includeClosed));
  return useQuery({
    queryKey: ["positions", strategyKey, includeClosed],
    queryFn: () => apiGet<PositionView[]>(`/api/positions?${params.toString()}`),
    refetchInterval: REFRESH_MS,
  });
}

export function usePositionDetail(positionId: string | null) {
  return useQuery({
    queryKey: ["position", positionId],
    queryFn: () => apiGet<PositionDetailView>(`/api/positions/${positionId}`),
    enabled: positionId !== null,
    refetchInterval: REFRESH_MS,
  });
}

export function useOrders(
  strategyKey: string,
  status: OrderStatus | "unconfirmed" | "",
  limit: number,
  offset: number,
) {
  const params = new URLSearchParams();
  if (strategyKey) {
    params.set("strategy_key", strategyKey);
  }
  if (status) {
    params.set("status", status);
  }
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  return useQuery({
    queryKey: ["orders", strategyKey, status, limit, offset],
    queryFn: () => apiGet<OrderPage>(`/api/orders?${params.toString()}`),
    refetchInterval: REFRESH_MS,
  });
}

const COMMAND_POLL_MS = 3_000;

export function useCommands(strategyKey = "", limit = 20) {
  const params = new URLSearchParams();
  if (strategyKey) {
    params.set("strategy_key", strategyKey);
  }
  params.set("limit", String(limit));
  params.set("offset", "0");
  return useQuery({
    queryKey: ["commands", strategyKey, limit],
    queryFn: () => apiGet<CommandPage>(`/api/commands?${params.toString()}`),
    refetchInterval: COMMAND_POLL_MS,
  });
}

export interface SubmitCommandInput {
  strategyKey: string;
  action: Exclude<CommandAction, "kill_switch">;
  idempotencyKey: string;
}

export function useSubmitCommand() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: SubmitCommandInput) =>
      apiPost<CommandView>(
        `/api/strategies/${encodeURIComponent(input.strategyKey)}/commands`,
        {
          action: input.action,
          idempotency_key: input.idempotencyKey,
        },
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["commands"] });
    },
  });
}

export function useKillSwitch() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (idempotencyKey: string) =>
      apiPost<CommandView>("/api/commands/kill-switch", {
        idempotency_key: idempotencyKey,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["commands"] });
    },
  });
}

export function useMarketChart(
  symbol: string,
  timeframe: string,
  limit = 300,
) {
  const params = new URLSearchParams({
    symbol,
    timeframe,
    limit: String(limit),
  });
  return useQuery({
    queryKey: ["market", symbol, timeframe, limit],
    queryFn: () =>
      apiGet<MarketChartView>(`/api/market/chart?${params.toString()}`),
    refetchInterval: REFRESH_MS,
    retry: 1,
  });
}

export function useConfigs() {
  return useQuery({
    queryKey: ["configs"],
    queryFn: () => apiGet<StrategyConfigView[]>("/api/configs"),
    refetchInterval: REFRESH_MS,
  });
}

export function useConfigDefaults(enabled: boolean) {
  return useQuery({
    queryKey: ["config-defaults"],
    queryFn: () => apiGet<StrategyConfigPayload>("/api/configs/defaults"),
    enabled,
    staleTime: Infinity,
  });
}

export function useCreateConfig() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: StrategyConfigCreateRequest) =>
      apiPost<StrategyConfigView>("/api/configs", input),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["configs"] });
    },
  });
}

export interface UpdateConfigInput {
  strategyKey: string;
  body: StrategyConfigUpdateRequest;
}

export function useUpdateConfig() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: UpdateConfigInput) =>
      apiPut<StrategyConfigView>(
        `/api/configs/${encodeURIComponent(input.strategyKey)}`,
        input.body,
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["configs"] });
    },
  });
}
