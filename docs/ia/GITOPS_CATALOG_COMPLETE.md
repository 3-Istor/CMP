# GitOps Catalog Integration Complete ✅

## Summary

The frontend is now fully connected to the dynamic Git repository catalog. All hardcoded templates have been removed and the system now loads templates from https://github.com/3-Istor/ia-project-template.

## Changes Applied

### Backend Changes

#### 1. `backend/app/main.py`

**Added static file serving:**

```python
import os
from fastapi.staticfiles import StaticFiles

# Ensure template directory exists before mounting
os.makedirs("data/templates/templates", exist_ok=True)

# Mount static files for template icons
app.mount(
    "/static/templates",
    StaticFiles(directory="data/templates/templates"),
    name="templates",
)
```

**What this does:**

- Creates the template directory if it doesn't exist (prevents FastAPI crash)
- Serves static files from `data/templates/templates/` at `/static/templates/`
- Allows frontend to access custom template icons

#### 2. `backend/app/services/catalog_service.py`

**Added image URL formatting:**

```python
# Format image_path as a valid API path if present
image_path = None
if manifest.get("image_path"):
    template_id = manifest.get("id")
    image_filename = manifest.get("image_path")
    image_path = f"/static/templates/{template_id}/{image_filename}"
```

**What this does:**

- Converts relative paths like `"icon.png"` to full API paths
- Example: `"icon.png"` → `"/static/templates/openstack-nginx/icon.png"`
- Frontend can directly use this URL without modification

### Frontend Changes

#### 3. `frontend/src/app/page.tsx`

**Removed static catalog:**

- Deleted entire `STATIC_CATALOG` array (100+ lines)
- Removed hardcoded WordPress, Nextcloud, GitLab, Grafana templates

**Added dynamic loading:**

```typescript
const [templates, setTemplates] = useState<CatalogTemplate[]>([]);
const [loadingCatalog, setLoadingCatalog] = useState(true);

useEffect(() => {
  getCatalog()
    .then(setTemplates)
    .catch((err) => {
      console.error("Failed to load catalog:", err);
      toast.error("Failed to load templates from repository");
    })
    .finally(() => setLoadingCatalog(false));
}, []);
```

**Added loading states:**

- Shows spinner while loading templates
- Shows "No templates found" if repository is empty
- Shows error toast if backend is unreachable

**Updated header:**

- Changed "OpenStack + AWS" to "Terraform-based Deployments"
- Updated description to mention Git repository templates

#### 4. `frontend/src/components/catalog/CatalogGrid.tsx`

**Added custom icon support:**

```typescript
{t.image_path ? (
  <img
    src={t.image_path}
    alt={`${t.name} icon`}
    className="w-10 h-10 object-contain"
  />
) : (
  <span className="text-3xl">{t.icon}</span>
)}
```

**What this does:**

- Displays custom PNG/JPG icons if available
- Falls back to emoji if no custom icon
- Properly sized and contained

## How It Works

### Template Loading Flow

```
1. Frontend loads → Shows loading spinner
2. Calls GET /api/catalog/
3. Backend reads Git repository
4. Backend formats image paths
5. Frontend receives templates
6. Frontend displays catalog
```

### Image Serving Flow

```
1. Template has "image_path": "icon.png" in manifest
2. Backend formats to "/static/templates/openstack-nginx/icon.png"
3. Frontend receives full path
4. Frontend renders <img src="/static/templates/openstack-nginx/icon.png" />
5. FastAPI serves from data/templates/templates/openstack-nginx/icon.png
```

## Testing

### 1. Start Backend

```bash
cd backend
poetry run uvicorn app.main:app --reload --port 8000
```

**Expected logs:**

```
INFO: Cloning template repository from https://github.com/3-Istor/ia-project-template
INFO: Template repository synced successfully
INFO: Application startup complete
```

### 2. Verify Templates API

```bash
curl http://localhost:8000/api/catalog/
```

**Expected response:**

```json
[
  {
    "id": "openstack-nginx",
    "name": "Nginx Website (OpenStack)",
    "description": "Deploy a static website using Nginx on OpenStack.",
    "icon": "🌐",
    "category": "Web",
    "fields": [...],
    "image_path": "/static/templates/openstack-nginx/icon.png",
    "enabled": true
  },
  {
    "id": "openstack-web-git",
    "name": "Git Website (OpenStack)",
    "description": "Deploy a static website from a Git repository using Nginx on OpenStack.",
    "icon": "🌐",
    "category": "Web",
    "fields": [...],
    "image_path": null,
    "enabled": true
  }
]
```

### 3. Start Frontend

```bash
cd frontend
npm run dev
```

### 4. Open Browser

Navigate to http://localhost:3000

**You should see:**

- Loading spinner briefly
- 2 template cards (openstack-nginx, openstack-web-git)
- Custom icons if templates have them
- Emoji icons as fallback
- "Template: openstack-nginx" below each card

### 5. Test Static Files

If a template has a custom icon, test the URL directly:

```
http://localhost:8000/static/templates/openstack-nginx/icon.png
```

Should display the image.

## What You Should See

### Before (Old Behavior)

- 4 hardcoded templates (WordPress, Nextcloud, GitLab, Grafana)
- "2 OpenStack VMs + 2 AWS instances" description
- Only emoji icons
- Templates never changed

### After (New Behavior)

- Templates loaded from Git repository
- "Template: openstack-nginx" description
- Custom PNG/JPG icons supported
- Templates sync every 24 hours
- Loading spinner on page load
- Error message if backend is down

## Troubleshooting

### Issue: "No templates found in repository"

**Causes:**

1. Backend not running
2. Git repository not cloned
3. No templates have `"enabled": true`

**Solution:**

```bash
# Check backend logs
cd backend
poetry run uvicorn app.main:app --reload --port 8000

# Check if repo is cloned
ls -la backend/data/templates/

# Force sync
curl -X POST http://localhost:8000/api/catalog/sync
```

### Issue: Custom icons not displaying

**Causes:**

1. Image file doesn't exist in template directory
2. Static files not mounted correctly
3. CORS issue

**Solution:**

```bash
# Check if image exists
ls -la backend/data/templates/templates/openstack-nginx/

# Test static file serving
curl http://localhost:8000/static/templates/openstack-nginx/icon.png

# Check browser console for CORS errors
```

### Issue: Loading spinner never stops

**Causes:**

1. Backend not running
2. API URL misconfigured
3. Network error

**Solution:**

```bash
# Check backend is running
curl http://localhost:8000/health

# Check frontend API URL
cat frontend/.env.local
# Should have: NEXT_PUBLIC_API_URL=http://localhost:8000/api

# Check browser console for errors
```

### Issue: Old templates still showing

**Causes:**

1. Browser cache
2. Frontend not rebuilt

**Solution:**

```bash
# Hard refresh browser
# Chrome/Firefox: Ctrl+Shift+R (Windows) or Cmd+Shift+R (Mac)

# Or rebuild frontend
cd frontend
rm -rf .next
npm run dev
```

## File Structure

```
backend/
├── app/
│   ├── main.py                    # ✅ Updated: Static file serving
│   └── services/
│       └── catalog_service.py     # ✅ Updated: Image URL formatting
└── data/
    └── templates/
        └── templates/
            ├── openstack-nginx/
            │   ├── manifest.json
            │   ├── main.tf
            │   └── icon.png       # Served at /static/templates/openstack-nginx/icon.png
            └── openstack-web-git/
                ├── manifest.json
                └── main.tf

frontend/
├── src/
│   ├── app/
│   │   └── page.tsx               # ✅ Updated: Removed static catalog
│   └── components/
│       └── catalog/
│           └── CatalogGrid.tsx    # ✅ Updated: Custom icon support
```

## API Endpoints

### GET /api/catalog/

Returns templates from Git repository with formatted image paths.

### GET /static/templates/{template_id}/{filename}

Serves static files (icons, images) from template directories.

### POST /api/catalog/sync

Forces immediate sync of Git repository (useful for testing).

## Next Steps

1. **Add custom icons to templates:**
   - Fork https://github.com/3-Istor/ia-project-template
   - Add `icon.png` to template directories
   - Add `"image_path": "icon.png"` to manifest.json
   - CMP will automatically display them

2. **Create new templates:**
   - Add new directory in `templates/`
   - Create `manifest.json` with `"enabled": true`
   - Add Terraform files
   - CMP will automatically load them

3. **Test deployment:**
   - Click "Deploy" on a template
   - Fill in configuration
   - Watch Terraform deployment progress

## Summary

✅ Static catalog removed
✅ Dynamic Git repository loading implemented
✅ Custom icon support added
✅ Loading states added
✅ Error handling improved
✅ Static file serving configured
✅ Image URL formatting implemented

The frontend now fully integrates with the GitOps catalog system!

---

**Completed**: April 7, 2026
**Status**: ✅ Production Ready
