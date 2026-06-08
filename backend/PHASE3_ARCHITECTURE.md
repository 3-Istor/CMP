# Phase 3 Architecture Diagram

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         CMP Backend (FastAPI)                            │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │                    Saga Orchestrator                            │    │
│  │                                                                 │    │
│  │  ┌──────────────────────┐      ┌──────────────────────┐       │    │
│  │  │  LEGACY_HYBRID       │      │  KUBERNETES          │       │    │
│  │  │  (OpenStack + AWS)   │      │  (GitOps)            │       │    │
│  │  └──────────────────────┘      └──────────────────────┘       │    │
│  │           │                              │                     │    │
│  │           ↓                              ↓                     │    │
│  │  ┌──────────────────────┐      ┌──────────────────────┐       │    │
│  │  │ OpenStack Service    │      │ GitHub Service       │       │    │
│  │  │ AWS Service          │      │ Terraform Executor   │       │    │
│  │  └──────────────────────┘      └──────────────────────┘       │    │
│  └────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │                    Database (SQLite/PostgreSQL)                 │    │
│  │                                                                 │    │
│  │  Deployment Table:                                              │    │
│  │  - id, name, template_id, status                                │    │
│  │  - provider_type (LEGACY_HYBRID | KUBERNETES)                   │    │
│  │  - project_id, github_repo_url, argocd_app_name, k8s_namespace  │    │
│  │  - terraform_outputs, terraform_state_path                      │    │
│  └────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        │                           │                           │
        ↓                           ↓                           ↓
┌───────────────┐         ┌──────────────────┐       ┌──────────────────┐
│   OpenStack   │         │  GitHub API      │       │  Terraform       │
│   + AWS       │         │  (GitHub App)    │       │  S3 Backend      │
└───────────────┘         └──────────────────┘       └──────────────────┘
                                    │                           │
                                    │                           │
                                    ↓                           ↓
                          ┌──────────────────┐       ┌──────────────────┐
                          │  Private Repo    │       │  Dynamic State   │
                          │  - values.yaml   │       │  cmp/projects/   │
                          │  - Dockerfile    │       │  <proj>/<app>    │
                          │  - CI/CD         │       │  .tfstate        │
                          └──────────────────┘       └──────────────────┘
                                    │
                                    │
                                    ↓
                          ┌──────────────────────────────────────────┐
                          │         ArgoCD (GitOps Controller)       │
                          │                                          │
                          │  - Monitors GitHub repository            │
                          │  - Syncs values.yaml changes             │
                          │  - Deploys to K8s namespace              │
                          └──────────────────────────────────────────┘
                                    │
                                    │
                                    ↓
                          ┌──────────────────────────────────────────┐
                          │      K3s Cluster (Target Environment)    │
                          │                                          │
                          │  ┌────────────────────────────────────┐ │
                          │  │  Namespace: <project>-<app>        │ │
                          │  │                                    │ │
                          │  │  - Deployment (Pods)               │ │
                          │  │  - Service                         │ │
                          │  │  - HTTPRoute (Envoy Gateway)       │ │
                          │  │  - VaultSecret (VSO)               │ │
                          │  │  - SecurityPolicy (SSO)            │ │
                          │  └────────────────────────────────────┘ │
                          └──────────────────────────────────────────┘
```

## Kubernetes Deployment Sequence

```
┌─────────┐
│  User   │
└────┬────┘
     │ 1. POST /api/deployments
     │    {provider_type: "kubernetes"}
     ↓
┌─────────────────────────────────────────────────────────────────┐
│  FastAPI Backend                                                 │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ 2. Create Deployment record (status: PENDING)              │ │
│  │    - provider_type = KUBERNETES                            │ │
│  │    - project_id = "test-project"                           │ │
│  │    - app_config = {github_installation_id, replica_count}  │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              ↓                                   │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ 3. Background Task: saga_orchestrator.run_deployment()     │ │
│  │    - Detects provider_type == KUBERNETES                   │ │
│  │    - Routes to _run_kubernetes_deployment()                │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              ↓                                   │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ 4. GitHub Service: get_installation_token()                │ │
│  │    - Generate JWT (signed with private key)                │ │
│  │    - Exchange for installation token (1 hour TTL)          │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              ↓                                   │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ 5. Terraform Executor: _execute_terraform_kubernetes()     │ │
│  │    - Generate tfvars file                                  │ │
│  │    - terraform init -backend-config=key=<dynamic>          │ │
│  │    - terraform apply -auto-approve                         │ │
│  │    - terraform output -json                                │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  Terraform Module: github_bootstrap                              │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ 6a. GitHub Provider                                        │ │
│  │     - Create private repository                            │ │
│  │     - Push values.yaml, Dockerfile, CI/CD workflow         │ │
│  │     - Configure branch protection                          │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              ↓                                   │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ 6b. Kubernetes Provider                                    │ │
│  │     - Create namespace: <project>-<app>                    │ │
│  │     - Add labels: project, app, managed-by                 │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              ↓                                   │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ 6c. Vault Provider                                         │ │
│  │     - Create KV path: kvv2/projects/<proj>/<app>           │ │
│  │     - Generate random secrets (db password, api key)       │ │
│  │     - Create Kubernetes auth role                          │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              ↓                                   │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ 6d. Kubernetes Manifest (ArgoCD)                           │ │
│  │     - Create Secret: argocd-repo (GitHub App creds)        │ │
│  │     - Create Application CRD                               │ │
│  │       * source: GitHub repo URL                            │ │
│  │       * destination: K8s namespace                         │ │
│  │       * syncPolicy: automated, prune, selfHeal             │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  FastAPI Backend (continued)                                     │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ 7. Update Deployment record                                │ │
│  │    - github_repo_url = "https://github.com/..."           │ │
│  │    - argocd_app_name = "test-project-test-app"            │ │
│  │    - k8s_namespace = "test-project-test-app"              │ │
│  │    - terraform_outputs = {all outputs as JSON}            │ │
│  │    - status = RUNNING                                     │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  ArgoCD (Continuous Delivery)                                    │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ 8. Detect new Application CRD                              │ │
│  │    - Read GitHub repo URL from Application spec            │ │
│  │    - Authenticate using GitHub App credentials             │ │
│  │    - Clone repository                                      │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              ↓                                   │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ 9. Render Helm Chart                                       │ │
│  │    - Fetch Generic Microservice Helm Chart                 │ │
│  │    - Merge with deploy/values.yaml from repo               │ │
│  │    - Generate K8s manifests                                │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              ↓                                   │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ 10. Apply to K8s Cluster                                   │ │
│  │     - Create Deployment (Pods)                             │ │
│  │     - Create Service                                       │ │
│  │     - Create HTTPRoute (Envoy Gateway)                     │ │
│  │     - Create VaultSecret (VSO)                             │ │
│  │     - Create SecurityPolicy (if SSO enabled)               │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  K3s Cluster                                                     │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ 11. Application Running                                    │ │
│  │     - Pods: 2 replicas (from values.yaml)                  │ │
│  │     - Service: ClusterIP                                   │ │
│  │     - Ingress: Envoy Gateway HTTPRoute                     │ │
│  │     - Secrets: Injected by VSO from Vault                  │ │
│  │     - Network: Isolated by Cilium policies                 │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## State Management

```
┌─────────────────────────────────────────────────────────────────┐
│  S3 Bucket: 3-istor-tf-infra-aws                                 │
│                                                                  │
│  infra/                                                          │
│  └── k3s-master/                                                 │
│      └── terraform.tfstate          ← Static infrastructure     │
│                                                                  │
│  cmp/                                                            │
│  └── projects/                                                   │
│      ├── project-alpha/                                          │
│      │   ├── app-frontend.tfstate  ← Dynamic app state          │
│      │   └── app-backend.tfstate   ← Dynamic app state          │
│      └── project-beta/                                           │
│          └── app-api.tfstate       ← Dynamic app state          │
│                                                                  │
│  Benefits:                                                       │
│  ✓ No state locking conflicts                                   │
│  ✓ Parallel deployments possible                                │
│  ✓ Clear ownership boundaries                                   │
│  ✓ Easy rollback per application                                │
└─────────────────────────────────────────────────────────────────┘
```

## Provider Routing Logic

```python
def run_deployment(deployment_id: int, db: Session) -> None:
    deployment = db.get(Deployment, deployment_id)

    # ┌─────────────────────────────────────────────────────────┐
    # │              Provider Type Discriminator                 │
    # └─────────────────────────────────────────────────────────┘

    if deployment.provider_type == ProviderType.KUBERNETES:
        # ┌───────────────────────────────────────────────────┐
        # │  Modern GitOps Flow                               │
        # │  - GitHub App authentication                      │
        # │  - Terraform Day-0 bootstrapping                  │
        # │  - ArgoCD continuous delivery                     │
        # └───────────────────────────────────────────────────┘
        _run_kubernetes_deployment(deployment, db)
    else:
        # ┌───────────────────────────────────────────────────┐
        # │  Legacy SAGA Flow                                 │
        # │  - OpenStack VM provisioning                      │
        # │  - AWS ASG + ALB provisioning                     │
        # │  - Manual rollback on failure                     │
        # └───────────────────────────────────────────────────┘
        _run_legacy_hybrid_deployment(deployment, db)
```

## Security Layers

```
┌─────────────────────────────────────────────────────────────────┐
│  Security Layer 1: GitHub App Authentication                     │
│                                                                  │
│  JWT (10 min TTL)  →  Installation Token (1 hour TTL)           │
│  ✓ Granular permissions (read/write repos only)                 │
│  ✓ No long-lived PATs                                           │
│  ✓ Automatic token rotation                                     │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  Security Layer 2: Vault Secrets Management                      │
│                                                                  │
│  Auto-generated secrets (32+ chars)                             │
│  ✓ Namespace-bound Kubernetes auth roles                        │
│  ✓ Path-based access control                                    │
│  ✓ Secrets never in Git or database                             │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  Security Layer 3: Kubernetes RBAC                               │
│                                                                  │
│  ServiceAccount-based authentication                            │
│  ✓ Pods can only access their own namespace                     │
│  ✓ VSO bound to specific ServiceAccounts                        │
│  ✓ Network policies via Cilium                                  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  Security Layer 4: Terraform State Encryption                    │
│                                                                  │
│  S3 server-side encryption + DynamoDB locking                   │
│  ✓ State files encrypted at rest                                │
│  ✓ State locking prevents concurrent modifications              │
│  ✓ Versioning enabled for rollback                              │
└─────────────────────────────────────────────────────────────────┘
```
