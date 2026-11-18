/** 后端连接配置: 多后端列表 + 当前激活项, 持久化在 localStorage */

export interface BackendProfile {
  id: string;
  name: string;
  /** 空字符串表示同源（开发态走 Vite /api 代理, 服务器托管时指向同源后端） */
  baseUrl: string;
  apiKey: string;
}

export const LOCAL_BACKEND_ID = "local";

const BACKENDS_STORAGE_KEY = "smart-trader.backends.v1";
const ACTIVE_BACKEND_STORAGE_KEY = "smart-trader.active-backend.v1";

function envLocalBackend(): BackendProfile {
  return {
    id: LOCAL_BACKEND_ID,
    name: "本地",
    baseUrl: normalizeBaseUrl(import.meta.env.VITE_API_BASE_URL ?? ""),
    apiKey: import.meta.env.VITE_API_TOKEN ?? "",
  };
}

function isBackendProfile(value: unknown): value is BackendProfile {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    typeof record.id === "string" &&
    typeof record.name === "string" &&
    typeof record.baseUrl === "string" &&
    typeof record.apiKey === "string"
  );
}

function readStoredBackends(): BackendProfile[] {
  try {
    const raw = window.localStorage.getItem(BACKENDS_STORAGE_KEY);
    if (!raw) {
      return [];
    }
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed.filter(isBackendProfile);
  } catch {
    return [];
  }
}

/** 读取后端列表; 首次访问或本地条目缺失时用环境变量播种"本地" */
export function loadBackends(): BackendProfile[] {
  const stored = readStoredBackends();
  if (stored.some((profile) => profile.id === LOCAL_BACKEND_ID)) {
    return stored;
  }
  const seeded = [envLocalBackend(), ...stored];
  saveBackends(seeded);
  return seeded;
}

/** 保存后端列表; 本地条目始终存在 */
export function saveBackends(profiles: BackendProfile[]): void {
  const normalized = profiles.some((profile) => profile.id === LOCAL_BACKEND_ID)
    ? profiles
    : [envLocalBackend(), ...profiles];
  window.localStorage.setItem(
    BACKENDS_STORAGE_KEY,
    JSON.stringify(normalized),
  );
}

export function getActiveBackendId(): string {
  const stored = window.localStorage.getItem(ACTIVE_BACKEND_STORAGE_KEY);
  if (stored && loadBackends().some((profile) => profile.id === stored)) {
    return stored;
  }
  return LOCAL_BACKEND_ID;
}

export function setActiveBackendId(id: string): void {
  window.localStorage.setItem(ACTIVE_BACKEND_STORAGE_KEY, id);
}

export function getActiveBackend(): BackendProfile {
  const profiles = loadBackends();
  const activeId = getActiveBackendId();
  return (
    profiles.find((profile) => profile.id === activeId) ??
    profiles[0] ??
    envLocalBackend()
  );
}

/** 去掉尾部斜杠, 空值保持同源语义 */
export function normalizeBaseUrl(value: string): string {
  return value.trim().replace(/\/+$/, "");
}

export function newBackendId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `backend-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
