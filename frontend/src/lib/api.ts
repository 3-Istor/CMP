import type { CatalogTemplate, Deployment, TerraformOutputs } from "@/types";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
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
