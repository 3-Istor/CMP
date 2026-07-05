# Deployment API Requirements

**For Frontend Developers**

This document explains what data must be sent when creating a deployment.

---

## Problem

When creating a Kubernetes (k3s-gitops-app) deployment, Terraform fails with:

```
Error: No value for required variable "github_token"
Error: No value for required variable "project_name"
```

### Root Cause

The `app_config` sent by the frontend is incomplete. Currently it only contains:

```json
{
  "template_repo_name": "template-html-css",
  "app_type": "static",
  "github_owner": "3-Istor"
}
```

**Missing required fields**:

- ❌ `github_installation_id` - Needed to generate GitHub token
- ❌ `project_name` - Required by Terraform template

---

## Solution: Complete app_config

### For k3s-gitops-app Template

The frontend **MUST** send these fields in `app_config`:

```typescript
interface K8sGitOpsAppConfig {
  // Required - User selections
  template_repo_name: string; // e.g., "template-html-css"
  app_type: string; // e.g., "static" or "fullstack"
  github_owner: string; // e.g., "3-Istor"
  project_name: string; // e.g., "project-alpha" ⚠️ MISSING

  // Required - From user's GitHub link
  github_installation_id: number; // e.g., 12345678 ⚠️ MISSING

  // Optional - Infrastructure config
  replica_count?: number; // Default: 2
  sso_protected?: boolean; // Default: false
}
```

### Example API Call

```typescript
// 1. Get user's GitHub installation ID from their profile
const user = await fetchCurrentUser();
if (!user.github_installation_id) {
  showError("Please link your GitHub account first");
  return;
}

// 2. Get project name from form
const projectName = formData.project_name || "default";

// 3. Create deployment with COMPLETE app_config
const response = await fetch("/api/deployments", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    name: "my-app",
    template_id: "k3s-gitops-app",
    provider_type: "kubernetes",
    project_id: projectName, // Also set at deployment level
    app_config: {
      // User selections
      template_repo_name: "template-html-css",
      app_type: "static",
      github_owner: "3-Istor",

      // CRITICAL: Add these
      project_name: projectName,
      github_installation_id: user.github_installation_id,

      // Optional config
      replica_count: 2,
      sso_protected: false,
    },
  }),
});
```

---

## Backend Flow

### What Happens with Complete Data

1. **Frontend sends** complete `app_config` with `github_installation_id`
2. **Backend receives** request in `routers/deployments.py`
3. **Backend stores** deployment with `app_config` as JSON
4. **Background task** `terraform_orchestrator.run_deployment()` starts
5. **Backend generates** temporary GitHub token from `github_installation_id`:
   ```python
   installation_token = get_installation_token(
       int(app_config["github_installation_id"])
   )
   app_config["github_token"] = installation_token
   ```
6. **Terraform runs** with all required variables
7. **Deployment succeeds** ✅

### What Happens with Incomplete Data

1. **Frontend sends** incomplete `app_config` (missing `github_installation_id`)
2. **Backend stores** deployment
3. **Background task** starts
4. **Backend detects** missing `github_installation_id`
5. **Backend raises error**:
   ```
   ValueError: GitHub installation ID is required for k3s-gitops-app deployments.
   The frontend must provide 'github_installation_id' in app_config.
   ```
6. **Deployment fails** ❌

---

## Required Fields by Template

### k3s-project-bootstrap

```typescript
interface ProjectBootstrapConfig {
  project_name: string; // Required
  project_description?: string; // Optional
}
```

### k3s-gitops-app

```typescript
interface K8sGitOpsAppConfig {
  // Required from user input
  template_repo_name: string;
  app_type: string;
  github_owner: string;
  project_name: string;

  // Required from user's GitHub link
  github_installation_id: number;

  // Optional
  replica_count?: number;
  sso_protected?: boolean;
}
```

### Legacy Hybrid Templates

```typescript
interface LegacyHybridConfig {
  instance_type?: string;
  db_size?: string;
  // ... other legacy fields
}
```

---

## Frontend Checklist

Before creating a k3s-gitops-app deployment:

- [ ] User has linked their GitHub account
- [ ] `user.github_installation_id` is available
- [ ] `project_name` is selected or provided
- [ ] `template_repo_name` is selected
- [ ] `app_type` is selected (static/fullstack)
- [ ] `github_owner` is set (usually "3-Istor")
- [ ] All fields are included in `app_config`
- [ ] `project_id` is set at deployment level

---

## Example: CreateDeploymentModal.tsx

```typescript
async function handleSubmit(e: React.FormEvent) {
  e.preventDefault();

  // 1. Check GitHub link
  if (template.category === "paas") {
    if (!user.github_installation_id) {
      toast.error("Please link your GitHub account first");
      navigate("/account");
      return;
    }
  }

  // 2. Build complete app_config
  const app_config: any = {
    template_repo_name: formData.template_repo_name,
    app_type: formData.app_type || "static",
    github_owner: formData.github_owner || "3-Istor",
  };

  // 3. Add required fields for k3s-gitops-app
  if (template.id === "k3s-gitops-app") {
    app_config.project_name = formData.project_name || "default";
    app_config.github_installation_id = user.github_installation_id;

    // Optional fields
    if (formData.replica_count) {
      app_config.replica_count = formData.replica_count;
    }
    if (formData.sso_protected !== undefined) {
      app_config.sso_protected = formData.sso_protected;
    }
  }

  // 4. Create deployment
  const response = await fetch("/api/deployments", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: formData.name,
      template_id: template.id,
      provider_type: template.provider_type,
      project_id: formData.project_name || null,
      app_config: app_config,
    }),
  });

  if (response.ok) {
    toast.success("Deployment started");
    onClose();
  } else {
    const error = await response.json();
    toast.error(error.detail || "Deployment failed");
  }
}
```

---

## Backend Validation

The backend now validates and enriches the `app_config`:

```python
# In terraform_orchestrator.py run_deployment()

# 1. Inject app_name (always)
app_config["app_name"] = deployment.name

# 2. Inject project_name if missing
if "project_name" not in app_config:
    if deployment.project_id:
        app_config["project_name"] = deployment.project_id
    else:
        app_config["project_name"] = "default"

# 3. Generate GitHub token if installation_id provided
if "github_installation_id" in app_config:
    installation_token = get_installation_token(
        int(app_config["github_installation_id"])
    )
    app_config["github_token"] = installation_token

# 4. Validate for k3s-gitops-app
elif deployment.template_id == "k3s-gitops-app":
    if "github_token" not in app_config:
        raise ValueError(
            "GitHub installation ID is required. "
            "Frontend must provide 'github_installation_id' in app_config."
        )
```

---

## Error Messages

### Missing GitHub Installation ID

**Backend Error**:

```
ValueError: GitHub installation ID is required for k3s-gitops-app deployments.
The frontend must provide 'github_installation_id' in app_config.
User must link their GitHub account first.
```

**Frontend Should Show**:

```
"Please link your GitHub account before creating Kubernetes deployments"
[Link GitHub Account Button]
```

### Missing Project Name

**Terraform Error**:

```
Error: No value for required variable "project_name"
```

**Backend Fallback**:

- Uses `deployment.project_id` if available
- Uses `"default"` as last resort

**Frontend Should**:

- Always provide `project_name` in `app_config`
- Also set `project_id` at deployment level

---

## Testing

### Test 1: Complete Data

```bash
curl -X POST http://localhost:8000/api/deployments \
  -H "Content-Type: application/json" \
  -d '{
    "name": "test-app",
    "template_id": "k3s-gitops-app",
    "provider_type": "kubernetes",
    "project_id": "test-project",
    "app_config": {
      "template_repo_name": "template-html-css",
      "app_type": "static",
      "github_owner": "3-Istor",
      "project_name": "test-project",
      "github_installation_id": 12345678
    }
  }'
```

**Expected**: Deployment succeeds ✅

### Test 2: Missing github_installation_id

```bash
curl -X POST http://localhost:8000/api/deployments \
  -H "Content-Type: application/json" \
  -d '{
    "name": "test-app",
    "template_id": "k3s-gitops-app",
    "provider_type": "kubernetes",
    "app_config": {
      "template_repo_name": "template-html-css",
      "app_type": "static",
      "github_owner": "3-Istor"
    }
  }'
```

**Expected**: Deployment fails with clear error message ❌

---

## Summary

**Frontend must send**:

```typescript
{
  name: string,
  template_id: string,
  provider_type: string,
  project_id: string,  // Also in app_config as project_name
  app_config: {
    template_repo_name: string,
    app_type: string,
    github_owner: string,
    project_name: string,           // ⚠️ ADD THIS
    github_installation_id: number, // ⚠️ ADD THIS
  }
}
```

**Backend will**:

1. Validate data
2. Generate GitHub token from `github_installation_id`
3. Inject `app_name` automatically
4. Run Terraform with all required variables
5. Return success or detailed error

---

## See Also

- **Backend**: `backend/app/services/terraform_orchestrator.py`
- **API**: `backend/app/routers/deployments.py`
- **Schemas**: `backend/app/schemas/deployment.py`
- **Frontend Guide**: `.kiro/steering/docs/05-cmp-backend-api/03-cmp-frontend-integration.md`
