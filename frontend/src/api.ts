import type { Role } from "./types";

const BASE = "/api";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(
  path: string,
  role: Role,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-User-Login", role);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${BASE}${path}`, { ...init, headers });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = data?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((item: { msg?: string }) => item.msg).join("; ")
          : `HTTP ${response.status}`;
    throw new ApiError(response.status, message);
  }
  return data as T;
}

export const api = {
  me: (role: Role) => request<{ login: string; role: Role; display_name: string }>("/auth/me", role),
  dashboard: (role: Role) => request<import("./types").Dashboard>("/dashboard", role),
  predictions: (role: Role, query = "") =>
    request<import("./types").Prediction[]>(`/predictions${query}`, role),
  prediction: (role: Role, id: number) =>
    request<import("./types").Prediction>(`/predictions/${id}`, role),
  object: (role: Role, id: number) =>
    request<import("./types").ObjectCard>(`/objects/${id}`, role),
  events: (role: Role, objectId: number, limit = 40) =>
    request<import("./types").EventRow[]>(`/events?object_id=${objectId}&limit=${limit}`, role),
  decide: (
    role: Role,
    id: number,
    body: { decision: string; reason?: string; create_ticket?: boolean },
  ) =>
    request<import("./types").Prediction>(`/predictions/${id}/decision`, role, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  sendTicket: (role: Role, draftId: number) =>
    request<{ external_id: string; draft_id: number; status: string }>(
      `/integrations/tickets?draft_id=${draftId}`,
      role,
      { method: "POST" },
    ),
  metrics: (role: Role) => request<import("./types").Metrics>("/analytics/metrics", role),
  feedback: (role: Role) => request<import("./types").FeedbackStats>("/analytics/feedback", role),
  audit: (role: Role) => request<{ id: number; user_login: string; action: string; details: string; created_at: string }[]>("/audit?limit=30", role),
  streamTick: (role: Role, scenario = "mix", n = 8) =>
    request<{
      inserted: number;
      rules_written: number;
      latency_ms: number;
      predictions: import("./types").Prediction[];
    }>(`/stream/tick?scenario=${encodeURIComponent(scenario)}&n=${n}`, role, { method: "POST" }),
  calibrate: (role: Role) =>
    request<{ applied: { risk: string; base_threshold: number; threshold: number }[]; detail: string }>(
      "/analytics/retrain",
      role,
      { method: "POST" },
    ),
};
