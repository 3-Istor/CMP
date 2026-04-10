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
}

export interface TerraformOutputs {
  [key: string]: string | number | boolean | null;
}

export interface CatalogField {
  name: string;
  label: string;
  type: "text" | "number" | "select";
  default: string | number | null;
  options: string[] | null;
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
