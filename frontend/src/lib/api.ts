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

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  // Inject JWT token from NextAuth session
  if (typeof window !== "undefined") {
    const session = await getSession();
    if (session?.accessToken) {
      headers["Authorization"] = `Bearer ${session.accessToken}`;
    }
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
  return res.json() as Promise<T>;
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
  const session = await getSession();
  const res = await fetch(
    `${BASE}/projects/${project_name}/members/${username}`,
    {
      method: "DELETE",
      headers: {
        Authorization: `Bearer ${session?.accessToken}`,
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
  const session = await getSession();
  const headers: Record<string, string> = {};

  if (session?.accessToken) {
    headers["Authorization"] = `Bearer ${session.accessToken}`;
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
