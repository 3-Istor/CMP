import type { CatalogTemplate, Deployment, TerraformOutputs } from "@/types";
import { getSession, signIn } from "next-auth/react";

// Declare runtime config type
declare global {
  interface Window {
    __RUNTIME_CONFIG__?: {
      apiUrl: string;
    };
  }
}

// Use runtime config if available, otherwise fall back to build-time env var
export const getApiUrl = () => {
  if (typeof window !== "undefined" && window.__RUNTIME_CONFIG__) {
    return window.__RUNTIME_CONFIG__.apiUrl;
  }
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";
};

const BASE = getApiUrl();

// ── Access-token cache ────────────────────────────────────────────────────────
// `getSession()` performs a network round-trip to `/api/auth/session` on every
// call. With polling hooks (deployments refresh every 3s, plus the sidebar),
// that doubles the number of requests and gates every data fetch behind a
// session fetch. Cache the token briefly so repeated calls reuse it.
let _tokenCache: { token: string | null; at: number } | null = null;
let _tokenInflight: Promise<string | null> | null = null;
const TOKEN_TTL_MS = 30_000;

export async function getAccessToken(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  const now = Date.now();
  if (_tokenCache && now - _tokenCache.at < TOKEN_TTL_MS) {
    return _tokenCache.token;
  }
  // De-duplicate the session fetch: on first paint many hooks call this at
  // once — collapse them into a single getSession() round-trip.
  if (_tokenInflight) return _tokenInflight;

  _tokenInflight = (async () => {
    try {
      const session = await getSession();
      // The refresh token itself expired (session max reached): only a new
      // login can produce a usable token.
      if (session?.error === "RefreshAccessTokenError") {
        await signIn("keycloak");
        return null;
      }
      _tokenCache = { token: session?.accessToken ?? null, at: Date.now() };
      return _tokenCache.token;
    } catch {
      // Transient session-endpoint failure (common during dev startup):
      // fall back to the last known token rather than breaking the request.
      return _tokenCache?.token ?? null;
    } finally {
      _tokenInflight = null;
    }
  })();
  return _tokenInflight;
}

/** Drop the cached token (e.g. on logout) so the next call refetches it. */
export function clearTokenCache() {
  _tokenCache = null;
}

async function doRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  // Inject JWT token from NextAuth session (cached, see getAccessToken)
  const token = await getAccessToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${BASE}${path}`, {
    headers: { ...headers, ...options?.headers },
    credentials: "include",
    ...options,
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(err || `HTTP ${res.status}`);
  }

  // 204 No Content (e.g. DELETE endpoints) and empty bodies have no JSON to
  // parse — return undefined instead of throwing on res.json().
  if (res.status === 204) {
    return undefined as T;
  }
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

// Collapses concurrent identical GETs into a single network call (e.g. the
// sidebar and the page both load projects/deployments on mount).
const _inflight = new Map<string, Promise<unknown>>();

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const method = (options?.method ?? "GET").toUpperCase();
  if (method !== "GET" || options?.body) {
    return doRequest<T>(path, options);
  }

  const existing = _inflight.get(path);
  if (existing) return existing as Promise<T>;

  const promise = doRequest<T>(path, options);
  _inflight.set(path, promise);
  // Clean up on settle. Use then(handler, handler) — NOT .finally() — so this
  // bookkeeping branch handles rejection itself instead of leaving a floating
  // rejected promise (which surfaces as an unhandledRejection). The original
  // `promise` is returned to the caller, which handles its own errors.
  const cleanup = () => {
    _inflight.delete(path);
  };
  promise.then(cleanup, cleanup);
  return promise;
}

// Catalog
export const getCatalog = () => request<CatalogTemplate[]>("/catalog/");

export const getTemplate = (id: string) =>
  request<CatalogTemplate>(`/catalog/${id}`);

export const syncCatalog = () =>
  request<{ message: string }>("/catalog/sync", { method: "POST" });

// Deployments
function securityQuery(params: Record<string, string | undefined | null>) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) query.set(key, value);
  }
  return query.toString();
}

export const getSecurityOverview = () =>
  request<import("@/types").SecurityProjectOverview[]>("/security/overview");

export const getSecuritySummary = (
  project: string,
  app?: string | null,
  view: import("@/types").SecurityView = "developer",
) =>
  request<import("@/types").SecuritySummary>(
    `/security/summary?${securityQuery({ project, app, view })}`,
  );

export const getSecurityFindings = (
  project: string,
  app?: string | null,
  view: import("@/types").SecurityView = "developer",
  state: "open" | "excepted" | "resolved" = "open",
) =>
  request<import("@/types").SecurityFinding[]>(
    `/security/findings?${securityQuery({ project, app, view, state })}`,
  );

export const getSecurityTrend = (project: string, app?: string | null) =>
  request<import("@/types").SecurityTrendPoint[]>(
    `/security/trend?${securityQuery({ project, app, days: "90" })}`,
  );

export const getSecurityExceptions = (project: string, app?: string | null) =>
  request<import("@/types").SecurityException[]>(
    `/security/exceptions?${securityQuery({ project, app })}`,
  );

export const getSecurityRole = (project: string) =>
  request<{ role: "admin" | "member" | null; cnp_admin: boolean; username: string }>(
    `/security/me?${securityQuery({ project })}`,
  );

export const requestSecurityScan = (
  project: string,
  app: string,
  source: "ci" | "trivy-operator",
) =>
  request<import("@/types").SecurityScan>("/security/scans", {
    method: "POST",
    body: JSON.stringify({ project, app, source }),
  });

export const requestSecurityBackup = (project: string, app: string) =>
  request<{ backups: string[] }>("/security/backups", {
    method: "POST",
    body: JSON.stringify({ project, app }),
  });

export const createSecurityException = (
  fingerprint: string,
  payload: import("@/types").SecurityExceptionRequest,
) =>
  request<import("@/types").SecurityException>(
    `/security/findings/${encodeURIComponent(fingerprint)}/exception`,
    { method: "POST", body: JSON.stringify(payload) },
  );

export const approveSecurityException = (id: number) =>
  request<import("@/types").SecurityException>(
    `/security/exceptions/${id}/approve`,
    { method: "POST" },
  );

export const revokeSecurityException = (id: number) =>
  request<import("@/types").SecurityException>(`/security/exceptions/${id}`, {
    method: "DELETE",
  });

export const getDeployments = () => request<Deployment[]>("/deployments/");

export const getDeployment = (id: number) =>
  request<Deployment>(`/deployments/${id}`);

export const createDeployment = (payload: {
  name: string;
  template_id: string;
  project_id?: string | null;
  app_config: Record<string, unknown>;
}) =>
  request<Deployment>("/deployments/", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const deleteDeployment = (id: number) =>
  request<{ message: string; id: number }>(`/deployments/${id}`, {
    method: "DELETE",
  });

export const getDeploymentOutputs = (id: number) =>
  request<TerraformOutputs>(`/deployments/${id}/outputs`);

// Infrastructure Monitoring
export const getGlobalHealth = () =>
  request<import("@/types").GlobalHealthResponse>("/infra/health");

export const getAppHealth = (deploymentId: number) =>
  request<import("@/types").AppHealthResponse>(
    `/infra/deployments/${deploymentId}/health`,
  );

// Account & Profile
export const getCurrentUser = () =>
  request<import("@/types").UserProfile>("/account/me");

export const getGitHubStatus = async (): Promise<
  import("@/types").GitHubStatus
> => {
  const profile = await request<import("@/types").UserProfile>("/account/me");
  return { github_installation_id: profile.github_installation_id ?? null };
};

export const saveGitHubInstallationId = async (
  installation_id: string,
): Promise<import("@/types").GitHubInstallationResponse> => {
  return request<import("@/types").GitHubInstallationResponse>(
    "/account/github-installation",
    {
      method: "POST",
      body: JSON.stringify({ installation_id }),
    },
  );
};

// Projects (Phase 4)
export const getProjects = () =>
  request<import("@/types").Project[]>("/projects/");

export const createProject = (
  project_name: string,
  target_cloud: import("@/types").TargetCloud = "onprem",
) =>
  request<import("@/types").ProjectCreateResponse>("/projects/", {
    method: "POST",
    body: JSON.stringify({ project_name, target_cloud }),
  });

export const getProjectApps = (project_name: string) =>
  request<import("@/types").Deployment[]>(`/projects/${project_name}/apps`);

export const searchKeycloakUsers = (q: string) =>
  request<import("@/types").KeycloakUserResult[]>(
    `/projects/users/search?q=${encodeURIComponent(q)}`,
  );

// Project Members (Phase 4)
export const getProjectMembers = (project_name: string) =>
  request<import("@/types").ProjectMembersResponse>(
    `/projects/${project_name}/members`,
  );

export const addProjectMember = (
  project_name: string,
  username: string,
  role: "admin" | "member" = "member",
) =>
  request<import("@/types").AddMemberResponse>(
    `/projects/${project_name}/members`,
    {
      method: "POST",
      body: JSON.stringify({ username, role }),
    },
  );

export const removeProjectMember = async (
  project_name: string,
  username: string,
) => {
  const token = await getAccessToken();
  const res = await fetch(
    `${BASE}/projects/${project_name}/members/${username}`,
    {
      method: "DELETE",
      headers: {
        Authorization: `Bearer ${token}`,
      },
      credentials: "include",
    },
  );

  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return Promise.resolve();
};

// Day-2 GitOps Config (Phase 4)
const configPath = (id: number, component?: string) =>
  `/deployments/${id}/config` +
  (component ? `?component=${encodeURIComponent(component)}` : "");

export const getDeploymentConfig = (id: number, component?: string) =>
  request<import("@/types").DeploymentConfig>(configPath(id, component));

export const updateDeploymentConfig = (
  id: number,
  payload: Record<string, unknown> & { _sha: string },
  component?: string,
) =>
  request<import("@/types").DeploymentConfigUpdateResponse>(
    configPath(id, component),
    {
      method: "PATCH",
      body: JSON.stringify(payload),
    },
  );

export const getSecurityData = (id: number) =>
  request<import("@/types").SecurityData>(`/deployments/${id}/security-data`);

export const updateSecurityData = (
  id: number,
  payload: import("@/types").SecurityDataUpdate,
) =>
  request<{ repo: string; commits: Record<string, string> }>(
    `/deployments/${id}/security-data`,
    { method: "PUT", body: JSON.stringify(payload) },
  );

/** What saving would commit, file by file, without writing anything. */
export const previewSecurityData = (
  id: number,
  payload: import("@/types").SecurityDataUpdate,
) =>
  request<{
    repo: string;
    previews: Record<string, { message: string; diff: string }>;
  }>(`/deployments/${id}/security-data?dry_run=true`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });

export const uploadProfilePicture = async (file: File) => {
  const formData = new FormData();
  formData.append("file", file);

  // Get session to include JWT token
  const token = await getAccessToken();
  const headers: Record<string, string> = {};

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${BASE}/account/picture`, {
    method: "POST",
    body: formData,
    headers,
    credentials: "include",
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(err || `HTTP ${res.status}`);
  }

  return res.json() as Promise<import("@/types").PictureUploadResponse>;
};

export const deleteProject = (project_name: string) =>
  request<void>(`/projects/${project_name}`, {
    method: "DELETE",
  });

// ── FinOps ────────────────────────────────────────────────────────────────────

function finopsQuery(params: Record<string, string | number | undefined>): string {
  const qs = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== "" && v !== null)
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
    .join("&");
  return qs ? `?${qs}` : "";
}

export const getFinopsOverview = (opts: {
  project?: string;
  period?: string;
  granularity?: string;
} = {}) =>
  request<import("@/types").FinopsOverview>(
    `/finops/overview${finopsQuery(opts)}`,
  );

export const getFinopsTimeline = (opts: {
  project?: string;
  app?: number;
  resource?: string;
  granularity?: string;
  period?: string;
} = {}) =>
  request<import("@/types").CostSeriesPoint[]>(
    `/finops/timeline${finopsQuery(opts)}`,
  );

export const getFinopsBreakdown = (opts: { project?: string; app?: number } = {}) =>
  request<import("@/types").CostBreakdown>(
    `/finops/breakdown${finopsQuery(opts)}`,
  );

export const getFinopsApps = (project?: string) =>
  request<import("@/types").AppCostRow[]>(
    `/finops/apps${finopsQuery({ project })}`,
  );

export const getRecommendations = (project?: string) =>
  request<import("@/types").Recommendation[]>(
    `/finops/recommendations${finopsQuery({ project })}`,
  );

export const applyRecommendation = (recId: string) =>
  request<import("@/types").FinopsActionResponse>(
    `/finops/recommendations/${encodeURIComponent(recId)}/apply`,
    { method: "POST" },
  );

export const ignoreRecommendation = (recId: string) =>
  request<import("@/types").FinopsActionResponse>(
    `/finops/recommendations/${encodeURIComponent(recId)}/ignore`,
    { method: "POST" },
  );

export const notifyRecommendation = (recId: string) =>
  request<import("@/types").FinopsActionResponse>(
    `/finops/recommendations/${encodeURIComponent(recId)}/notify`,
    { method: "POST" },
  );

export const getBudget = (project_name: string) =>
  request<import("@/types").Budget | null>(
    `/finops/budgets/${encodeURIComponent(project_name)}`,
  );

export const putBudget = (
  project_name: string,
  payload: {
    monthly_amount_eur: number;
    threshold_warn: number;
    threshold_critical: number;
    currency?: string;
  },
) =>
  request<import("@/types").Budget>(
    `/finops/budgets/${encodeURIComponent(project_name)}`,
    { method: "PUT", body: JSON.stringify(payload) },
  );

export const getFinopsAlerts = (project?: string) =>
  request<import("@/types").CostAlert[]>(
    `/finops/alerts${finopsQuery({ project })}`,
  );

export interface SecurityPolicy {
  ci_fail_on: { value: "none" | "critical"; locked: boolean } | null;
}

export const getSecuritySettings = (project: string, app: string) =>
  request<{
    ci_fail_on: { value: "none" | "critical"; source: string; locked: boolean };
    policy: SecurityPolicy;
  }>(`/security/settings?${securityQuery({ project, app })}`);

export const updateSecuritySettings = (
  project: string,
  app: string,
  ciFailOn: "none" | "critical",
  dryRun: boolean,
) =>
  request<{ message: string; diff?: string; commit?: string }>(
    `/security/settings${dryRun ? "?dry_run=true" : ""}`,
    {
      method: "PUT",
      body: JSON.stringify({ project, app, ci_fail_on: ciFailOn }),
    },
  );

export const getSecurityPolicy = (project: string) =>
  request<SecurityPolicy>(`/security/policy?${securityQuery({ project })}`);

export const updateSecurityPolicy = (project: string, policy: SecurityPolicy) =>
  request<{ updated: string[]; failed: string[] }>("/security/policy", {
    method: "PUT",
    body: JSON.stringify({ project, policy }),
  });

export interface SecurityBackup {
  name: string;
  database: string;
  phase: string;
  method: string;
  started_at: string | null;
  stopped_at: string | null;
  error: string | null;
  manual: boolean;
  backup_id: string | null;
  restorable: boolean;
}

export interface SecurityDatabase {
  name: string;
  phase: string;
  healthy: boolean;
  instances: number;
  ready_instances: number;
  backups_enabled: boolean;
  restored_from: string | null;
  restored_backup: string | null;
  restored_to: string | null;
}

export const getSecurityDatabases = (project: string, app: string) =>
  request<SecurityDatabase[]>(`/security/databases?${securityQuery({ project, app })}`);

export const requestSecurityRestore = (
  payload: { project: string; app: string; backup: string; target_time?: string },
  dryRun: boolean,
) =>
  request<{ message: string; diff?: string; commit?: string }>(
    `/security/restores${dryRun ? "?dry_run=true" : ""}`,
    { method: "POST", body: JSON.stringify(payload) },
  );

export const getSecurityBackups = (project: string, app: string) =>
  request<SecurityBackup[]>(`/security/backups?${securityQuery({ project, app })}`);

export interface SecurityWebhook {
  configured: boolean;
  hint: string | null;
}

export interface SecurityAlertTargets {
  project: SecurityWebhook;
  app: SecurityWebhook | null;
  platform: SecurityWebhook;
  effective: "app" | "project" | "platform" | null;
}

export const getSecurityAlertTargets = (project: string, app: string | null) =>
  request<SecurityAlertTargets>(
    `/security/alerts?${securityQuery(app ? { project, app } : { project })}`,
  );

export const setSecurityAlertTarget = (
  project: string,
  app: string | null,
  webhookUrl: string | null,
) =>
  request<SecurityAlertTargets>("/security/alerts", {
    method: "PUT",
    body: JSON.stringify({ project, app, webhook_url: webhookUrl }),
  });

export const testSecurityAlertTarget = (project: string, app: string | null) =>
  request<void>("/security/alerts/test", {
    method: "POST",
    body: JSON.stringify({ project, app }),
  });

// ── Activity tab ──────────────────────────────────────────────────────────────

function activityQuery(
  project: string | null,
  f: import("@/types").ActivityFilters,
) {
  const params = new URLSearchParams(project ? { project } : {});
  if (f.source) params.set("source", f.source);
  if (f.actor) params.set("actor", f.actor);
  if (f.action) params.set("action", f.action);
  if (f.app) params.set("app", f.app);
  if (f.include_reads) params.set("include_reads", "true");
  if (f.notable_only) params.set("notable_only", "true");
  const until = f.until ? new Date(f.until) : new Date();
  params.set("until", until.toISOString());
  const since = new Date(until.getTime() - (f.days ?? 7) * 86_400_000);
  params.set("since", since.toISOString());
  return params.toString();
}

export const getActivity = (
  project: string | null,
  filters: import("@/types").ActivityFilters,
) =>
  request<import("@/types").ActivityFeed>(
    `/activity?${activityQuery(project, filters)}&limit=100`,
  );

export const getActivitySummary = (project: string | null, days = 7) =>
  request<import("@/types").ActivitySummary>(
    `/activity/summary?${new URLSearchParams({
      ...(project ? { project } : {}),
      days: String(days),
    })}`,
  );

export const getDeniedFlows = (project: string, days = 1) =>
  request<import("@/types").DeniedFlow[]>(
    `/activity/network?${new URLSearchParams({ project, days: String(days) })}`,
  );

export const getPlatformStatus = () =>
  request<import("@/types").PlatformStatus>("/activity/platform");

export async function downloadActivity(
  project: string | null,
  filters: import("@/types").ActivityFilters,
  format: "csv" | "json",
) {
  const token = await getAccessToken();
  const res = await fetch(
    `${BASE}/activity/export?${activityQuery(project, filters)}&format=${format}`,
    {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      credentials: "include",
    },
  );
  if (!res.ok) throw new Error((await res.text()) || `HTTP ${res.status}`);
  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = `activity-${project ?? "platform"}.${format}`;
  link.click();
  URL.revokeObjectURL(url);
}

export const getProjectLogs = (
  project: string,
  f: import("@/types").LogFilters,
) => {
  const params = new URLSearchParams({ project });
  for (const key of [
    "namespace",
    "pod",
    "container",
    "search",
    "level",
    "since",
    "until",
  ] as const) {
    const value = f[key];
    if (value) params.set(key, value);
  }
  params.set("limit", String(f.limit ?? 500));
  return request<import("@/types").LogLine[]>(`/activity/logs?${params}`);
};

export const getLogTargets = (project: string) =>
  request<import("@/types").LogTargets>(
    `/activity/logs/targets?${new URLSearchParams({ project })}`,
  );

export const getLogVolume = (project: string, hours: number, namespace?: string) =>
  request<import("@/types").LogVolume>(
    `/activity/logs/volume?${new URLSearchParams({
      project,
      hours: String(hours),
      ...(namespace ? { namespace } : {}),
    })}`,
  );

// ── Alerts tab ────────────────────────────────────────────────────────────────

function alertingQuery(project: string, app?: string | null) {
  return new URLSearchParams({ project, ...(app ? { app } : {}) }).toString();
}

export const getAlertCatalog = (project: string, app?: string | null) =>
  request<import("@/types").AlertCatalog>(`/alerting?${alertingQuery(project, app)}`);

export const updateCatalogAlert = (
  project: string,
  app: string | null | undefined,
  alertId: string,
  enabled: boolean,
  params: Record<string, number>,
) =>
  request<import("@/types").CatalogAlert>(
    `/alerting/${alertId}?${alertingQuery(project, app)}`,
    { method: "PUT", body: JSON.stringify({ enabled, params }) },
  );
