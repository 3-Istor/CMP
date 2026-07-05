import type { CatalogTemplate, Deployment, TerraformOutputs } from "@/types";
import { getSession } from "next-auth/react";

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

async function getAccessToken(): Promise<string | null> {
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

export const createProject = (project_name: string) =>
  request<import("@/types").ProjectCreateResponse>("/projects/", {
    method: "POST",
    body: JSON.stringify({ project_name }),
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
export const getDeploymentConfig = (id: number) =>
  request<import("@/types").DeploymentConfig>(`/deployments/${id}/config`);

export const updateDeploymentConfig = (
  id: number,
  payload: Record<string, unknown> & { _sha: string },
) =>
  request<import("@/types").DeploymentConfigUpdateResponse>(
    `/deployments/${id}/config`,
    {
      method: "PATCH",
      body: JSON.stringify(payload),
    },
  );

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
