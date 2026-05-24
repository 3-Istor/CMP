variable "project_name" {
  description = "The CNP Project name (used for namespace prefix and isolation)"
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]+$", var.project_name))
    error_message = "Project name must be lowercase alphanumeric with hyphens only"
  }
}

variable "app_name" {
  description = "The application name"
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]+$", var.app_name))
    error_message = "App name must be lowercase alphanumeric with hyphens only"
  }
}

variable "replica_count" {
  description = "Initial number of pod replicas"
  type        = number
  default     = 2

  validation {
    condition     = var.replica_count >= 1 && var.replica_count <= 10
    error_message = "Replica count must be between 1 and 10"
  }
}

variable "sso_protected" {
  description = "Whether to enable Keycloak SSO protection via Envoy Gateway"
  type        = bool
  default     = false
}

variable "github_installation_id" {
  description = "GitHub App Installation ID (from user's Keycloak profile)"
  type        = string
  sensitive   = true
}

variable "github_app_id" {
  description = "CNP GitHub App ID"
  type        = string
  default     = "3836905"
}

variable "github_app_private_key" {
  description = "CNP GitHub App Private Key (PEM format)"
  type        = string
  sensitive   = true
}
