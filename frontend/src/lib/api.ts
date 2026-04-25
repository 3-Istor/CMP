import type { CatalogTemplate, Deployment, TerraformOutputs } from "@/types";

// Declare runtime config type
declare global {
  interface Window {
    __RUNTIME_CONFIG__?: {
      apiUrl: string;
    };
  }
}

// Use runtime config if available, otherwise fall back to build-time env var
const getApiUrl = () => {
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

  if (process.env.NEXT_PUBLIC_DEV_TOKEN) {
    headers["Authorization"] = `Bearer ${process.env.NEXT_PUBLIC_DEV_TOKEN}`;
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

export const uploadProfilePicture = async (file: File) => {
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(`${BASE}/account/picture`, {
    method: "POST",
    body: formData,
    credentials: "include", // Forward cookies to gateway
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(err || `HTTP ${res.status}`);
  }

  return res.json() as Promise<import("@/types").PictureUploadResponse>;
};
