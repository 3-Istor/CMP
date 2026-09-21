# Grafana Integration — Quick Start

## 🎯 What It Does

Automatically syncs project members from Keycloak to Grafana Organizations so developers can access their project's dashboards without manual Grafana administration.

## ⚙️ Setup (1 minute)

### 1. Add to `.env`:

```bash
GRAFANA_ADMIN_PASSWORD="your_grafana_admin_password"
```

### 2. Restart the backend:

```bash
poetry run uvicorn app.main:app --reload
```

## ✅ Verification

### Quick Test

```bash
poetry run python test_grafana_service.py
```

### Manual Test Flow

1. **Create a project** via CMP UI (e.g., "alpha")
2. **Login to Grafana** → Check "Project Alpha" org
3. **Add a member** via CMP Members panel
4. **Check Grafana** → Member should appear in the org

## 🔧 How It Works

### Integration Points

| Event          | Action                                |
| -------------- | ------------------------------------- |
| Create project | Creator added as Admin to Grafana org |
| Add member     | User added to Grafana org with role   |
| Remove member  | User removed from Grafana org         |

### Role Mapping

| CMP Role      | Grafana Role |
| ------------- | ------------ |
| admin / owner | Admin        |
| member        | Editor       |

### Organization Naming

| Project Name | Grafana Org Name |
| ------------ | ---------------- |
| alpha        | Project Alpha    |
| my-team      | Project My Team  |
| sandbox      | Project Sandbox  |

## 📁 Files Modified/Created

**New Files**:

- `app/services/grafana_service.py` — Grafana API client
- `test_grafana_service.py` — Test suite
- `GRAFANA_INTEGRATION.md` — Full documentation
- `GRAFANA_QUICKSTART.md` — This file

**Modified Files**:

- `app/routers/projects.py` — Added Grafana sync calls
- `app/main.py` — Added HTTP client cleanup
- `app/core/config.py` — Already had `GRAFANA_ADMIN_PASSWORD`

## 🐛 Troubleshooting

### "User not found in Grafana"

**Solution**: User needs to log in to Grafana once via OIDC to create their account:

```
https://grafana.3istor.com
→ Login with Keycloak
→ Re-add via CMP (idempotent)
```

### "Org not found"

**Solution**: Wait for Terraform bootstrap to complete (~10 seconds after project creation)

### "HTTP 401 Unauthorized"

**Solution**: Check `GRAFANA_ADMIN_PASSWORD` in `.env`:

```bash
# Test credentials
curl -u admin:$GRAFANA_ADMIN_PASSWORD https://grafana.3istor.com/api/org
```

## 📚 Full Documentation

See `GRAFANA_INTEGRATION.md` for:

- Complete architecture details
- Error handling strategies
- Security considerations
- Future enhancements
- API reference

## 🚀 Next Steps

Once verified working:

1. ✅ **Monitor logs** for Grafana sync operations
2. ✅ **Train team** on first-time login requirement
3. 🔄 **Consider** adding periodic reconciliation job
4. 🔒 **Consider** replacing admin user with service account
