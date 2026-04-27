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

// Infrastructure Monitoring Types
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

// Account & Profile Types
export interface UserProfile {
  sub: string;
  email: string;
  given_name: string | null;
  family_name: string | null;
  name: string | null;
  picture: string | null;
  groups: string[];
}

export interface PictureUploadResponse {
  message: string;
  picture_url: string;
}
