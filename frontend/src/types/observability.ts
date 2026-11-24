export interface ObsPoint {
  time: string;
  value: number;
}

export interface ObsCapabilities {
  metrics: boolean;
  logs: boolean;
  traces: boolean;
  alerts: boolean;
  slo: boolean;
  silence_max_hours: number;
  grafana_url: string;
}

export interface RouteSignals {
  route: string;
  requests_per_second: number;
  error_percent: number;
  p50_ms: number | null;
  p95_ms: number | null;
}

export interface GoldenSignals {
  range: string;
  step_seconds: number;
  no_traffic: boolean;
  rate: ObsPoint[];
  error_percent: ObsPoint[];
  p50_ms: ObsPoint[];
  p95_ms: ObsPoint[];
  routes: RouteSignals[];
}

export interface SloSettings {
  project: string;
  app: string;
  period_days: number;
  availability: number;
  latency: number;
  latency_threshold_ms: number;
  can_edit: boolean;
}

export interface SloObjective {
  kind: "availability" | "latency";
  target: number;
  budget_remaining: number | null;
  error_ratio: number | null;
  burn_rate_1h: number | null;
  burn_rate_6h: number | null;
  trend: ObsPoint[];
}

export interface SloStatus {
  settings: SloSettings;
  objectives: SloObjective[];
}

export interface DeployMarker {
  time: string;
  finished_at: string | null;
  version: string | null;
  strategy: string;
  outcome: string;
  component: string | null;
}

export interface Dora {
  days: number;
  deployments: number;
  failed_changes: number;
  deploys_per_week: number;
  change_failure_rate: number | null;
}

export interface ErrorPattern {
  pattern: string;
  count: number;
  trace_id: string | null;
  last_seen: string | null;
}

export interface ObsLogLine {
  time: string;
  message: string;
  pod: string;
  container: string;
  level: string | null;
  trace_id: string | null;
}

export interface TraceSummary {
  trace_id: string;
  root_service: string;
  root_operation: string;
  start: string;
  duration_ms: number;
  spans: number;
  errors: number;
  services: string[];
}

export interface TraceSpan {
  span_id: string;
  parent_id: string | null;
  service: string;
  operation: string;
  start_us: number;
  duration_us: number;
  error: boolean;
  tags: Record<string, string>;
}

export interface TraceDetail extends TraceSummary {
  truncated: boolean;
  span_list: TraceSpan[];
}

export interface ObsAlert {
  fingerprint: string;
  state: string;
  silenced_by: string[];
  starts_at: string;
  labels: Record<string, string>;
  summary: string | null;
  description: string | null;
  value: string | null;
  runbook_url: string | null;
}

export interface SilenceMatcher {
  name: string;
  value: string;
}

export interface ObsSilence {
  id: string;
  matchers: SilenceMatcher[];
  starts_at: string;
  ends_at: string;
  created_by: string;
  comment: string;
}

export interface AppAlerts {
  alerts: ObsAlert[];
  silences: ObsSilence[];
}

export interface WasteHint {
  title: string;
  rec_type: string;
  monthly_saving_eur: number;
  justification: string;
  text_key?: string | null;
  text_params?: Record<string, string | number>;
}

export interface AppCosts {
  days: number;
  cost_eur: number;
  daily: { date: string; total_eur: number }[];
  requests: number | null;
  metrics_available: boolean;
  cost_per_1k_requests_eur: number | null;
  usage: {
    cpu_request_cores: number | null;
    cpu_p95_cores: number | null;
    ram_request_gib: number | null;
    ram_p99_gib: number | null;
    history_days: number;
  } | null;
  cpu_idle_ratio: number | null;
  ram_idle_ratio: number | null;
  recommendations: WasteHint[];
  monthly_waste_eur: number;
}
