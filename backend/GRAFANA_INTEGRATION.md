# Grafana Integration — Automatic User Synchronization

## Overview

The CMP backend now automatically synchronizes project membership to Grafana Organizations, enabling developers to access their project's private dashboards and metrics without manual Grafana user management.

## Problem Statement

**Challenge**: Grafana Open Source doesn't support dynamic mapping of OIDC users to multiple custom organizations via groups.

**Our Setup**:

- Users authenticate to Grafana via Keycloak OIDC SSO (username in Grafana = username in Keycloak)
- Each project has a dedicated Grafana Organization (e.g., "Project Alpha", "Project Sandbox")
- Each project has isolated DataSources for security (no cross-project data access)
- Terraform creates these Grafana Organizations during project bootstrap

**Solution**: The CMP backend programmatically syncs Keycloak project membership to Grafana Organizations via the Grafana Admin API.

## Architecture

### Grafana Service (`app/services/grafana_service.py`)

Async service for managing Grafana organization memberships:

- **Add/Update User**: `add_user_to_project_org(project_name, username, role) -> bool`
- **Remove User**: `remove_user_from_project_org(project_name, username) -> bool`

**Features**:

- ✅ Async operations with connection pooling (httpx)
- ✅ Graceful degradation (logs warnings, doesn't crash on failure)
- ✅ Idempotent operations (safe to retry)
- ✅ Automatic role updates (if user already in org)

### Integration Points

The service is called at key lifecycle events in `app/routers/projects.py`:

1. **Project Creation**: Creator added as Admin to Grafana org
2. **Add Member**: New member added to Grafana org with appropriate role
3. **Remove Member**: User removed from Grafana org

## Configuration

### Required Environment Variable

Add to `backend/.env`:

```bash
GRAFANA_ADMIN_PASSWORD="your_grafana_admin_password"
```

This is the password for the Grafana `admin` user (used for API authentication).

### Grafana API Details

- **Base URL**: `https://grafana.3istor.com`
- **Authentication**: Basic Auth (username: `admin`, password: from `.env`)
- **Organization Naming**: `Project <TitleCasedProjectName>`
  - `alpha` → `Project Alpha`
  - `my-team` → `Project My Team`
  - `sandbox` → `Project Sandbox`

### Role Mappings

| CMP Role | Grafana Role | Permissions                              |
| -------- | ------------ | ---------------------------------------- |
| admin    | Admin        | Full org control, edit dashboards        |
| owner    | Admin        | Full org control, edit dashboards        |
| member   | Editor       | View dashboards, create temporary panels |

## API Integration

### When a Project is Created

```python
# backend/app/routers/projects.py

@router.post("/", response_model=ProjectCreateResponse)
async def create_project(...):
    # ... Terraform bootstrap creates Grafana org ...

    # Add creator as admin (after 8s delay for Terraform)
    async def add_creator_to_project():
        add_user_to_project(username, project_name, "admin")  # Keycloak
        await add_user_to_project_org(project_name, username, "admin")  # Grafana

    background_tasks.add_task(add_creator_to_project)
```

### When a Member is Added

```python
# backend/app/routers/projects.py

@router.post("/{project_name}/members")
async def add_project_member(...):
    # Add to Keycloak group
    add_user_to_project(username, project_name, role)

    # Sync to Grafana (non-blocking, best-effort)
    await add_user_to_project_org(project_name, username, role)
```

### When a Member is Removed

```python
# backend/app/routers/projects.py

@router.delete("/{project_name}/members/{username}")
async def remove_project_member(...):
    # Remove from Keycloak groups
    remove_user_from_project(username, project_name)

    # Sync to Grafana (non-blocking, best-effort)
    await remove_user_from_project_org(project_name, username)
```

## Error Handling & Resilience

### Graceful Degradation

The Grafana service follows a **best-effort** approach:

- ✅ Logs warnings on failure (doesn't crash the main request)
- ✅ Returns `False` on API errors (caller can decide how to handle)
- ✅ Works even if Grafana is temporarily unreachable

**Example**: If Grafana is down during member addition, the user is still added to Keycloak and will eventually sync once Grafana is back online (next login or manual retry).

### Expected "Failures"

These are logged as warnings but are NOT errors:

1. **Org not found**: Terraform hasn't created the Grafana org yet (bootstrap in progress)
2. **User not found**: User hasn't logged in to Grafana yet (no account created)

Once the user logs in for the first time (via OIDC), their Grafana account is auto-created and subsequent sync attempts will succeed.

### Retry Strategy

The system is **idempotent** — you can safely re-add a user:

- If user already exists in the org, their role is updated (no error)
- If user doesn't exist, they're added (409 → PATCH workflow)

## Testing

### Unit Tests

```bash
cd backend
poetry run python test_grafana_service.py
```

**Tests**:

1. Configuration check (GRAFANA_ADMIN_PASSWORD)
2. Helper functions (org name formatting, role mapping)
3. Interactive API tests (add/update/remove user)

### Manual Testing

1. **Create a project** via CMP UI
2. **Check Grafana**: Creator should be Admin in "Project <Name>" org
3. **Add a member** via Members panel
4. **Check Grafana**: Member should be Editor in the org
5. **Remove the member**
6. **Check Grafana**: Member should be removed from the org

## Troubleshooting

### Issue: User not syncing to Grafana

**Symptoms**: User added via CMP but doesn't appear in Grafana org

**Possible causes**:

1. **User hasn't logged in to Grafana yet**
   - Solution: Ask user to log in once via OIDC (https://grafana.3istor.com)
   - After first login, re-add them via CMP (idempotent operation)

2. **Grafana org doesn't exist yet**
   - Solution: Wait for Terraform bootstrap to complete (~10s)
   - Check: `terraform state list` in project bootstrap state

3. **Wrong Grafana admin password**
   - Solution: Verify `GRAFANA_ADMIN_PASSWORD` in `.env`
   - Test: `curl -u admin:password https://grafana.3istor.com/api/org`

### Issue: "Failed to add user to Grafana org: HTTP 401"

**Cause**: Invalid Grafana admin credentials

**Solution**:

1. Check `GRAFANA_ADMIN_PASSWORD` in `.env`
2. Test credentials: `curl -u admin:$GRAFANA_ADMIN_PASSWORD https://grafana.3istor.com/api/admin/users`
3. If invalid, reset Grafana admin password

### Issue: "Failed to add user to Grafana org: HTTP 404"

**Cause**: Grafana org doesn't exist yet

**Solution**:

- Wait for Terraform bootstrap to complete
- Check Grafana UI: https://grafana.3istor.com/admin/orgs
- If missing, re-run Terraform: `terraform apply` in `k3s-project-bootstrap`

## Implementation Details

### Async HTTP Client

The service uses a **shared httpx.AsyncClient** for connection pooling:

```python
_http_client: httpx.AsyncClient | None = None

def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            auth=(GRAFANA_ADMIN_USERNAME, settings.GRAFANA_ADMIN_PASSWORD),
            timeout=10.0,
            follow_redirects=True,
        )
    return _http_client
```

**Cleanup**: The client is closed on application shutdown via `lifespan` handler in `main.py`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Shutdown
    from app.services.grafana_service import close_http_client
    await close_http_client()
```

### Grafana API Endpoints Used

| Endpoint                                    | Method | Purpose                 |
| ------------------------------------------- | ------ | ----------------------- |
| `/api/orgs/name/{org_name}`                 | GET    | Get org ID by name      |
| `/api/users/lookup?loginOrEmail={username}` | GET    | Get user ID by username |
| `/api/orgs/{org_id}/users`                  | POST   | Add user to org         |
| `/api/orgs/{org_id}/users/{user_id}`        | PATCH  | Update user role in org |
| `/api/orgs/{org_id}/users/{user_id}`        | DELETE | Remove user from org    |

## Security Considerations

### Credentials Management

- ✅ Grafana admin password stored in `.env` (never in code)
- ✅ Basic Auth over HTTPS only
- ✅ No credentials logged

### Principle of Least Privilege

- ❌ Currently uses `admin` account (superuser)
- ✅ **TODO**: Create a dedicated service account with limited permissions
  - Required permissions: `org:admin` (manage org users)
  - Scope: All organizations

**Improvement**:

```bash
# Create service account in Grafana
grafana-cli admin reset-admin-password newpassword
# Or via Terraform: grafana_service_account resource
```

## Future Enhancements

### 1. Sync on User Login

Currently, sync happens on membership change. Consider adding:

- **Grafana webhook** → CMP endpoint on user first login
- Trigger: `user_created` event
- Action: Sync all project memberships for that user

### 2. Periodic Reconciliation

Add a background job to reconcile drift:

```python
# Every 15 minutes
async def reconcile_grafana_memberships():
    projects = db.query(Project).all()
    for project in projects:
        members = list_project_members(project.name)
        for member in members:
            await add_user_to_project_org(
                project.name, member['username'], member['role']
            )
```

### 3. Service Account Authentication

Replace `admin` user with a dedicated service account:

```hcl
# terraform/k3s-project-bootstrap/grafana.tf
resource "grafana_service_account" "cmp_sync" {
  name = "cmp-membership-sync"
  role = "Admin"  # Org-level admin, not super admin
}

resource "grafana_service_account_token" "cmp_sync" {
  service_account_id = grafana_service_account.cmp_sync.id
  name              = "cmp-backend"
}
```

Then use the token in CMP:

```python
GRAFANA_SERVICE_ACCOUNT_TOKEN = settings.GRAFANA_SERVICE_ACCOUNT_TOKEN
headers = {"Authorization": f"Bearer {GRAFANA_SERVICE_ACCOUNT_TOKEN}"}
```

## Related Files

- `app/services/grafana_service.py` — Core Grafana API client
- `app/routers/projects.py` — Integration points (create/add/remove)
- `test_grafana_service.py` — Test suite
- `app/core/config.py` — Configuration (GRAFANA_ADMIN_PASSWORD)
- `app/main.py` — HTTP client lifecycle (shutdown cleanup)

## References

- [Grafana HTTP API](https://grafana.com/docs/grafana/latest/developers/http_api/)
- [Grafana Organizations](https://grafana.com/docs/grafana/latest/administration/organization-management/)
- [httpx Async Client](https://www.python-httpx.org/async/)
