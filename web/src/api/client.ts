const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "";
const TOKEN: string | undefined = import.meta.env.VITE_API_TOKEN;

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function headers(): Record<string, string> {
  const result: Record<string, string> = { Accept: "application/json" };
  if (TOKEN) {
    result.Authorization = `Bearer ${TOKEN}`;
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

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, { headers: headers() });
  return parse<T>(response);
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return parse<T>(response);
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    method: "PUT",
    headers: { ...headers(), "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return parse<T>(response);
}

export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
