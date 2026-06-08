---
inclusion: always
---

# Product Overview: Cloud Native Platform (CNP)

## Purpose & Vision

CNP is an Internal Developer Portal (IDP). It is a self-service platform designed to abstract cloud and infrastructure complexities for developers.

## Key Features

1. **Multi-Provider Architecture:**
   - **IaaS / Hybrid Catalog (Legacy):** Allows provisioning of virtual machines across OpenStack and AWS (Auto Scaling Groups).
   - **PaaS / GitOps Catalog (Modern):** Allows provisioning of containerized microservices deployed directly onto Kubernetes (K3s) using GitOps (ArgoCD).
2. **Strict Multi-Tenancy:** Isolation of resources (Networks, Secrets, Access Control) between logical "Projects".
