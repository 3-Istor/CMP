export type DeploymentStatus =
  | "pending"
  | "deploying_openstack"
  | "deploying_aws"
  | "running"
  | "degraded"
  | "rolling_back"
  | "failed"
  | "deleting"
  | "deleted";

export interface Deployment {
  id: number;
  name: string;
  template_id: string;
  status: DeploymentStatus;
  step_message: string;
  os_vm_db1_ip: string | null;
  os_vm_db2_ip: string | null;
  aws_alb_dns: string | null;
  aws_asg_name: string | null;
  created_at: string;
  updated_at: string;
}

export interface DeploymentHealth {
  status: "healthy" | "degraded" | "unknown" | "error" | "not_deployed";
  healthy: number;
  total: number;
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
}
