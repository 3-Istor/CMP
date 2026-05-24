terraform {
  required_version = ">= 1.5"

  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.0"
    }
    vault = {
      source  = "hashicorp/vault"
      version = "~> 4.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.30"
    }
  }
}

# ═══════════════════════════════════════════════════════════════════════════
# GitHub Repository Provisioning
# ═══════════════════════════════════════════════════════════════════════════

resource "github_repository" "app" {
  name        = var.app_name
  description = "Application provisioned by CNP for Project ${var.project_name}"
  visibility  = "private"

  # Enable features
  has_issues   = true
  has_wiki     = false
  has_projects = false

  # Security
  vulnerability_alerts = true

  # Template initialization
  auto_init = true
}

# Branch protection on main
resource "github_branch_protection" "main" {
  repository_id = github_repository.app.node_id
  pattern       = "main"

  enforce_admins = false

  required_pull_request_reviews {
    dismiss_stale_reviews           = true
    require_code_owner_reviews      = false
    required_approving_review_count = 0 # Allow direct push for now
  }
}

# Push initial template code
resource "github_repository_file" "values_yaml" {
  repository = github_repository.app.name
  branch     = "main"
  file       = "deploy/values.yaml"
  content = templatefile("${path.module}/templates/values.yaml.tftpl", {
    app_name      = var.app_name
    project_name  = var.project_name
    replica_count = var.replica_count
    sso_protected = var.sso_protected
  })
  commit_message      = "chore: Initialize CNP GitOps configuration"
  commit_author       = "CNP Platform"
  commit_email        = "platform@3istor.com"
  overwrite_on_create = true
}

resource "github_repository_file" "dockerfile" {
  repository          = github_repository.app.name
  branch              = "main"
  file                = "Dockerfile"
  content             = file("${path.module}/templates/Dockerfile")
  commit_message      = "chore: Add default Dockerfile"
  commit_author       = "CNP Platform"
  commit_email        = "platform@3istor.com"
  overwrite_on_create = true
}

resource "github_repository_file" "ci_workflow" {
  repository = github_repository.app.name
  branch     = "main"
  file       = ".github/workflows/ci.yml"
  content = templatefile("${path.module}/templates/ci.yml.tftpl", {
    app_name = var.app_name
  })
  commit_message      = "chore: Add CI/CD pipeline"
  commit_author       = "CNP Platform"
  commit_email        = "platform@3istor.com"
  overwrite_on_create = true
}

# ═══════════════════════════════════════════════════════════════════════════
# Kubernetes Namespace
# ═══════════════════════════════════════════════════════════════════════════

resource "kubernetes_namespace" "app" {
  metadata {
    name = "${var.project_name}-${var.app_name}"

    labels = {
      "project"                            = var.project_name
      "app"                                = var.app_name
      "managed-by"                         = "cnp"
      "pod-security.kubernetes.io/enforce" = "restricted"
    }
  }
}

# ═══════════════════════════════════════════════════════════════════════════
# Vault Secrets Path & Role
# ═══════════════════════════════════════════════════════════════════════════

resource "vault_generic_secret" "app_secrets" {
  path = "kvv2/data/projects/${var.project_name}/${var.app_name}"

  data_json = jsonencode({
    database-password = random_password.db_password.result
    api-key           = random_password.api_key.result
  })
}

resource "random_password" "db_password" {
  length  = 32
  special = true
}

resource "random_password" "api_key" {
  length  = 64
  special = false
}

# Vault Kubernetes Auth Role for the application
resource "vault_kubernetes_auth_backend_role" "app" {
  backend   = "kubernetes"
  role_name = "${var.project_name}-${var.app_name}-role"

  bound_service_account_names      = ["vault-secrets-operator"]
  bound_service_account_namespaces = [kubernetes_namespace.app.metadata[0].name]

  token_ttl     = 3600
  token_max_ttl = 7200

  token_policies = [
    "default",
    vault_policy.app_secrets.name
  ]
}

resource "vault_policy" "app_secrets" {
  name = "${var.project_name}-${var.app_name}-policy"

  policy = <<EOT
path "kvv2/data/projects/${var.project_name}/${var.app_name}/*" {
  capabilities = ["read", "list"]
}
path "kvv2/metadata/projects/${var.project_name}/${var.app_name}/*" {
  capabilities = ["read", "list"]
}
EOT
}

# ═══════════════════════════════════════════════════════════════════════════
# ArgoCD Application Registration
# ═══════════════════════════════════════════════════════════════════════════

# ArgoCD Repository Secret (GitHub App credentials)
resource "kubernetes_secret" "argocd_repo" {
  metadata {
    name      = "${var.project_name}-${var.app_name}-repo"
    namespace = "argocd"

    labels = {
      "argocd.argoproj.io/secret-type" = "repository"
    }
  }

  data = {
    type                    = "git"
    url                     = github_repository.app.html_url
    githubAppID             = var.github_app_id
    githubAppInstallationID = var.github_installation_id
    githubAppPrivateKey     = var.github_app_private_key
  }
}

# ArgoCD Application CRD
resource "kubernetes_manifest" "argocd_app" {
  manifest = {
    apiVersion = "argoproj.io/v1alpha1"
    kind       = "Application"

    metadata = {
      name      = "${var.project_name}-${var.app_name}"
      namespace = "argocd"

      annotations = {
        "argocd.argoproj.io/compare-options" = "ServerSideDiff=true,IncludeMutationWebhook=true"
      }
    }

    spec = {
      project = var.project_name

      source = {
        repoURL        = github_repository.app.html_url
        targetRevision = "HEAD"
        path           = "."

        helm = {
          valueFiles = ["deploy/values.yaml"]
        }
      }

      destination = {
        server    = "https://kubernetes.default.svc"
        namespace = kubernetes_namespace.app.metadata[0].name
      }

      syncPolicy = {
        automated = {
          prune    = true
          selfHeal = true
        }

        syncOptions = [
          "ServerSideApply=true",
          "CreateNamespace=false"
        ]
      }
    }
  }
}
