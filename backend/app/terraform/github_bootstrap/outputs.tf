output "github_repo_url" {
  description = "The HTTPS URL of the created GitHub repository"
  value       = github_repository.app.html_url
}

output "github_repo_clone_url" {
  description = "The Git clone URL"
  value       = github_repository.app.ssh_url
}

output "k8s_namespace" {
  description = "The Kubernetes namespace where the app is deployed"
  value       = kubernetes_namespace.app.metadata[0].name
}

output "argocd_app_name" {
  description = "The ArgoCD Application name"
  value       = "${var.project_name}-${var.app_name}"
}

output "vault_path" {
  description = "The Vault KV path for application secrets"
  value       = "kvv2/data/projects/${var.project_name}/${var.app_name}"
}
