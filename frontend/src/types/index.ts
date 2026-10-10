export type DeploymentStatus =
  | "pending"
  | "initializing"
  | "planning"
  | "deploying"
  | "running"
  | "degraded"
  | "failed"
  | "deleting"
  | "deleted";

export type ProviderType = "legacy_hybrid" | "kubernetes";

export interface Deployment {
  id: number;
  name: string;
  template_id: string;
  template_name: string | null;
  template_icon: string | null;
  template_category: string | null;
  status: DeploymentStatus;
  step_message: string;
  terraform_outputs: string | null; // JSON string
  resource_count: number | null;
  created_at: string;
  updated_at: string;

  // Phase 3: Multi-Provider Support
  provider_type: ProviderType;
  project_id: string | null;
  github_repo_url: string | null;
  argocd_app_name: string | null;
  k8s_namespace: string | null;
}

// ── Projects (Phase 4) ────────────────────────────────────────────────────────

export type ProjectRole = "owner" | "admin" | "member";

/** The cloud a project's workloads run on. Immutable after creation. */
export type TargetCloud = "onprem" | "aws" | "gcp";

export type ProjectStatus =
  | "provisioning"
  | "active"
  | "suspended"
  | "decommissioning"
  | "failed"
  | "decommission_failed";

export interface Project {
  name: string;
  role: ProjectRole;
  target_cloud: TargetCloud;
  status: ProjectStatus;
  /** What the bootstrap or teardown is doing now, or why it failed. */
  step_message: string | null;
  /** False while the project's Keycloak groups do not exist. */
  is_accessible: boolean;
}

export function isProjectFailed(project: Project): boolean {
  return project.status === "failed" || project.status === "decommission_failed";
}

export function isProjectInFlight(project: Project): boolean {
  return (
    project.status === "provisioning" || project.status === "decommissioning"
  );
}

export interface ProjectCreateResponse {
  message: string;
  project_name: string;
  target_cloud: TargetCloud;
  status: string;
}

export interface ProjectMember {
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  role: ProjectRole;
}

export interface ProjectMembersResponse {
  project_name: string;
  members: ProjectMember[];
}

export interface AddMemberResponse {
  message: string;
  project_name: string;
  username: string;
  role: string;
}

// ── Day-2 GitOps Config (Phase 4) ─────────────────────────────────────────────

export interface DeploymentConfig {
  repo: string;
  file_path: string;
  /** Selectable components; empty when the app has a single values file. */
  components: string[];
  /** File SHA — must be echoed back in PATCH requests */
  _sha: string;
  config: Record<string, unknown>;
}

export interface DeploymentConfigUpdateResponse {
  message: string;
  repo: string;
  file_path: string;
  commit_sha: string;
  changed_keys: string[];
}

export type ExposurePreset =
  | "public"
  | "project_users"
  | "project_members"
  | "project_admins";

export interface SecurityData {
  repo: string;
  can_edit: boolean;
  exposure: ExposurePreset | "custom" | null;
  database: {
    backup: {
      enabled: boolean;
      keep_after_delete: boolean;
      retention_policy: string | null;
    };
  } | null;
}

export interface SecurityDataUpdate {
  exposure?: ExposurePreset;
  backup?: {
    enabled?: boolean;
    keep_after_delete?: boolean;
    retention_policy?: string;
  };
}

// ── Terraform / Catalog ───────────────────────────────────────────────────────

export interface TerraformOutputs {
  [key: string]: string | number | boolean | null;
}

export interface CatalogField {
  name: string;
  label: string;
  type: "text" | "number" | "select";
  default: string | number | null;
  options: string[] | null;
  required?: boolean;
}

export interface CatalogTemplate {
  id: string;
  name: string;
  description: string;
  icon: string;
  category: string;
  fields: CatalogField[];
  image_path?: string | null;
  enabled?: boolean;
}

// ── Infrastructure Monitoring ─────────────────────────────────────────────────

export interface VPNStatus {
  name: string;
  status: string;
  ip: string | null;
}

export interface HypervisorStatus {
  name: string;
  state: string;
  status: string;
  ip: string | null;
}

export interface GlobalHealthResponse {
  openstack_vpn: VPNStatus | null;
  aws_vpns: VPNStatus[];
  openstack_hypervisors: HypervisorStatus[];
}

export interface VMInstance {
  instance_id: string;
  private_ip: string | null;
  state: string;
  health: string | null;
}

export interface AWSFrontendHealth {
  asg_name: string;
  desired_capacity: number;
  instances: VMInstance[];
  healthy_count: number;
  total_count: number;
}

export interface OpenStackBackendHealth {
  servers: VMInstance[];
  healthy_count: number;
  total_count: number;
}

export type AppHealthStatus = "healthy" | "degraded" | "down" | "unknown";

export interface AppHealthResponse {
  deployment_name: string;
  status: AppHealthStatus;
  aws_frontend: AWSFrontendHealth | null;
  openstack_backend: OpenStackBackendHealth | null;
}

// ── Account & Profile ─────────────────────────────────────────────────────────

export interface UserProfile {
  sub: string;
  email: string;
  given_name: string | null;
  family_name: string | null;
  name: string | null;
  picture: string | null;
  groups: string[];
  github_installation_id?: string | null; // Phase 3: GitHub App integration
}

export interface GitHubStatus {
  github_installation_id: string | null;
}

export interface GitHubInstallationResponse {
  message: string;
  installation_id: string;
}

export interface PictureUploadResponse {
  message: string;
  picture_url: string;
}

export interface KeycloakUserResult {
  username: string;
  email: string;
  first_name: string;
  last_name: string;
}

// ── FinOps ────────────────────────────────────────────────────────────────────

export type FinopsResource = "cpu" | "ram" | "storage" | "network";

export interface CostBreakdown {
  cpu: number;
  ram: number;
  storage: number;
  network: number;
}

export interface CostSummary {
  month_to_date_eur: number;
  projected_month_eur: number;
  previous_month_eur: number;
  trend_pct: number | null;
  breakdown: CostBreakdown;
  potential_savings_eur: number;
  app_count: number;
  currency: string;
}

export interface CostSeriesPoint {
  date: string;
  cpu: number;
  ram: number;
  storage: number;
  network: number;
  total: number;
}

export interface AppCostRow {
  app_id: number;
  name: string;
  project_id: string | null;
  cost_per_day_eur: number;
  cost_month_estimate_eur: number;
  month_to_date_eur: number;
  trend_pct: number | null;
}

export interface Budget {
  project_name: string;
  monthly_amount_eur: number;
  threshold_warn: number;
  threshold_critical: number;
  currency: string;
  spent_eur: number;
  remaining_eur: number;
  consumed_pct: number | null;
  status: "ok" | "warning" | "critical";
  updated_by: string | null;
  updated_at: string | null;
}

export interface FinopsOverview {
  summary: CostSummary;
  budget: Budget | null;
  apps: AppCostRow[];
  timeline: CostSeriesPoint[];
}

export type RecommendationType =
  | "replicas"
  | "rightsizing"
  | "inactivity"
  | "storage";

export interface Recommendation {
  id: string;
  app_id: number;
  app_name: string;
  project_id: string | null;
  rec_type: RecommendationType;
  title: string;
  justification: string;
  current: Record<string, string | number>;
  recommended: Record<string, string | number>;
  monthly_saving_eur: number;
  confidence: number;
  effort: "low" | "medium" | "high";
  status: "pending" | "applied" | "ignored" | "notified";
  can_apply: boolean;
}

export interface CostAlert {
  id: number;
  project_name: string;
  app_id: number | null;
  level: "info" | "warning" | "critical";
  kind: "budget" | "spike";
  message: string;
  value_pct: number | null;
  triggered_at: string;
}

export interface FinopsActionResponse {
  message: string;
  rec_id: string;
  status: string;
  commit_sha: string | null;
}

export type SecurityTier = "core" | "important" | "recommended" | "info";
export type SecurityCategory =
  | "leaks"
  | "dependencies"
  | "container"
  | "access"
  | "data"
  | "journal";
export type SecurityView = "developer" | "platform";

export interface SecurityException {
  id: number;
  app: string | null;
  fingerprint: string;
  rule: string;
  kind: "vulnerability" | "secret" | "other";
  status: "not_affected" | "false_positive" | "accepted_risk" | "revoked";
  justification: string | null;
  statement: string;
  expires_on: string;
  author: string;
  created_at: string;
  approved_by: string | null;
  approved_at: string | null;
  revoked_by: string | null;
  revoked_at: string | null;
  pending_approval: boolean;
}

export interface SecurityFinding {
  fingerprint: string;
  app: string | null;
  category: SecurityCategory;
  tier: SecurityTier;
  audience: "developer" | "platform";
  rule: string;
  title: string;
  detail: string;
  fix: string;
  location: string;
  link: string | null;
  raw: string;
  sources: string[];
  first_seen: string;
  last_seen: string;
  resolved_at: string | null;
  exception: SecurityException | null;
}

export interface SecuritySegment {
  category: SecurityCategory;
  label: string;
  worst: SecurityTier | null;
  counts: Partial<Record<SecurityTier, number>>;
  points_lost: number;
}

export interface SecurityScan {
  app: string | null;
  source: "kyverno" | "trivy-operator" | "ci" | "cnpg" | "exposure" | "cilium";
  status: "ok" | "error" | "missing" | "unavailable";
  message: string;
  last_run_at: string | null;
  next_run_at: string | null;
  collected_at: string | null;
  requested_at: string | null;
}

export interface SecurityAppSummary {
  app: string;
  deployment_id: number | null;
  score: number;
  grade: string;
  actions: number;
  core: number;
}

export interface SecuritySummary {
  project: string;
  app: string | null;
  view: SecurityView;
  score: number;
  grade: string;
  actions: number;
  core: number;
  excepted: number;
  segments: SecuritySegment[];
  apps: SecurityAppSummary[];
  scans: SecurityScan[];
  guarantees: { label: string; ok: boolean }[];
}

export interface SecurityTrendPoint {
  day: string;
  score: number;
  grade: string;
  core: number;
  important: number;
  recommended: number;
  new_major: number;
}

export interface SecurityProjectOverview {
  project: string;
  score: number;
  grade: string;
  actions: number;
  core: number;
}

export interface SecurityExceptionRequest {
  project: string;
  status: SecurityException["status"];
  justification: string | null;
  statement: string;
  expires_on: string | null;
}

// ── Activity tab ──────────────────────────────────────────────────────────────

export type ActivitySource =
  | "cmp"
  | "deployment"
  | "kubernetes"
  | "vault"
  | "keycloak";

export interface ActivityEvent {
  id: string;
  time: string;
  source: ActivitySource;
  actor: string;
  action: string;
  notable: boolean;
  project: string | null;
  app: string | null;
  target: string;
  outcome: "success" | "failure";
  status_code: number | null;
  source_ip: string | null;
  details: Record<string, unknown>;
}

export interface ActivityFeed {
  events: ActivityEvent[];
  unavailable: ActivitySource[];
}

export interface ActivityKpi {
  value: number;
  previous: number;
}

export interface ActivitySummary {
  actions: ActivityKpi;
  people: ActivityKpi;
  notable: ActivityKpi;
  login_failures: ActivityKpi | null;
  days: { day: string; counts: Partial<Record<ActivitySource, number>> }[];
  top_actors: [string, number][];
  unavailable: ActivitySource[];
}

export interface ActivityFilters {
  source?: ActivitySource | null;
  actor?: string | null;
  action?: string | null;
  app?: string | null;
  days?: number;
  until?: string | null;
  include_reads?: boolean;
  notable_only?: boolean;
}

export interface LogLine {
  time: string;
  namespace: string;
  pod: string;
  container: string;
  line: string;
}

export interface LogTargets {
  namespaces: string[];
  pods: string[];
  containers: string[];
}

export interface LogFilters {
  namespace?: string | null;
  pod?: string | null;
  container?: string | null;
  search?: string | null;
  level?: "error" | "warn" | null;
  since?: string | null;
  until?: string | null;
  limit?: number;
}
