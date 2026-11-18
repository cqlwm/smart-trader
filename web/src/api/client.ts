import { getActiveBackend } from "./backends";
import type { BackendProfile } from "./backends";
import type { HealthView } from "./types";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function headers(profile: BackendProfile, json: boolean): Record<string, string> {
  const result: Record<string, string> = { Accept: "application/json" };
  if (json) {
    result["Content-Type"] = "application/json";
  }
  if (profile.apiKey) {
    result.Authorization = `Bearer ${profile.apiKey}`;
  }
  return result;
}

async function errorMessage(response: Response): Promise<string> {
  const body = await response.text();
  try {
    const parsed: unknown = JSON.parse(body);
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      "detail" in parsed &&
      typeof parsed.detail === "string"
    ) {
      return parsed.detail;
    }
  } catch {
    // 非 JSON 响应直接使用原文
  }
  return body || `HTTP ${response.status} ${response.statusText}`;
}

async function parse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new ApiError(response.status, await errorMessage(response));
  }
  return (await response.json()) as T;
}

async function request<T>(
  profile: BackendProfile,
  path: string,
  init: RequestInit,
): Promise<T> {
  const response = await fetch(`${profile.baseUrl}${path}`, init);
  return parse<T>(response);
}

export async function apiGet<T>(path: string): Promise<T> {
  const profile = getActiveBackend();
  return request<T>(profile, path, { headers: headers(profile, false) });
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const profile = getActiveBackend();
  return request<T>(profile, path, {
    method: "POST",
    headers: headers(profile, true),
    body: JSON.stringify(body),
  });
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const profile = getActiveBackend();
  return request<T>(profile, path, {
    method: "PUT",
    headers: headers(profile, true),
    body: JSON.stringify(body),
  });
}

/** 用指定配置探测连通性（管理弹窗中的"测试连接"） */
export async function testBackendConnection(
  profile: BackendProfile,
): Promise<HealthView> {
  return request<HealthView>(profile, "/api/health", {
    headers: headers(profile, false),
  });
}

export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
