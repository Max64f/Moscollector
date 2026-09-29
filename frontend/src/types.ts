export type Role = "dispatcher" | "analyst" | "manager";

export type Prediction = {
  id: number;
  object_id: number | null;
  object_name: string | null;
  channel_id: number | null;
  risk_type: string;
  risk_title: string;
  probability: number;
  horizon_hours: number;
  explanation: string | null;
  recommendation: string | null;
  status: string;
  source: string;
  created_at: string | null;
  feedback?: Feedback[];
  ticket_id?: number | null;
  decision?: string;
};

export type Feedback = {
  id: number;
  decision: string;
  decision_title: string;
  reason: string | null;
  user_login: string | null;
  created_at: string | null;
};

export type Dashboard = {
  open_total: number;
  objects_at_risk: number;
  open_by_risk: Record<string, number>;
  risk_titles: Record<string, string>;
  critical: Prediction[];
  top: Prediction[];
  alarm_window: { from: string | null; to: string | null };
  season?: { month: number | null; flood_season: boolean; heating_season: boolean };
  stream_slo_seconds?: number;
};

export type Picket = {
  picket: string;
  n_channels: number;
  sensor_types: string[];
  has_fire: boolean;
  has_flood: boolean;
  has_guard: boolean;
};

export type ObjectCard = {
  id: number;
  name: string;
  kind: string;
  level: number;
  ancestors: { id: number; kind: string; name: string; level: number }[];
  children: { id: number; kind: string; name: string; level: number }[];
  n_channels: number;
  n_pickets: number;
  pickets: Picket[];
  predictions: Prediction[];
};

export type EventRow = {
  event_id: number;
  event_ts: string;
  channel_id: number;
  object_id: number | null;
  object_name: string | null;
  sensor_type: string | null;
  picket: string | null;
  value_raw: string | null;
  value_cat: string | null;
  risk: string | null;
};

export type Metrics = {
  available: boolean;
  trained_at?: string;
  split?: { train: number; valid: number; test: number };
  test_lgbm?: {
    risk: string;
    title: string;
    precision: number;
    recall: number;
    pr_auc: number;
    threshold: number;
    calibrated_threshold?: number;
  }[];
  calibration?: {
    risk: string;
    title: string;
    threshold: number;
    n_dispatch: number;
    n_false_alarm: number;
    updated_at: string | null;
  }[];
  retrained_at?: string;
  note?: string;
  detail?: string;
};

export type FeedbackStats = {
  predictions_by_risk_status: Record<string, Record<string, number>>;
  decisions: { decision: string; title: string; n: number }[];
  open: number;
  closed: number;
};

export const RISK_COLOR: Record<string, string> = {
  fire: "#e85d4c",
  flood: "#3d8fd1",
  failure: "#d4a017",
  intrusion: "#9b6bdb",
};

export const DECISIONS = [
  { id: "dispatch", label: "Выезд" },
  { id: "false_alarm", label: "Ложное" },
  { id: "watch", label: "Наблюдение" },
  { id: "maintenance", label: "ТО" },
] as const;

export function pct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${Math.round(value * 100)}%`;
}
