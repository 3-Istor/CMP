# Quick Deploy Guide - Test Your App NOW

## ✅ Status: Frontend is FIXED and Ready

The frontend has been updated to send all required data to the backend. You can now deploy Kubernetes apps!

---

## 🚀 Step-by-Step: Deploy Your First App

### Step 1: Link Your GitHub Account (One-time setup)

#### Option A: Automatic (Recommended)

1. **Go to your account page**: http://localhost:3000/account
2. **Find the "GitHub Integration" section** (scroll down)
3. **Click "Link GitHub Account"** button (opens GitHub in new tab)
4. **On GitHub**: Select which account/org to install to
5. **On GitHub**: Click "Install & Authorize"
6. **Important**: GitHub will redirect you back - **copy the URL** if it doesn't auto-save
7. **Back in the app**: You should see "GitHub account linked" with your Installation ID

#### Option B: Manual (If automatic doesn't work)

If the callback doesn't work, you can enter the installation ID manually:

1. **Install the app on GitHub**: https://github.com/apps/cnp-portal/installations/new
2. **After installation**, go to: https://github.com/settings/installations
3. **Click "Configure"** next to "CNP-Portal"
4. **Look at the URL** - it will be like: `https://github.com/settings/installations/12345678`
5. **Copy the number** at the end (that's your installation_id)
6. **Go to** http://localhost:3000/account
7. **Find "Already installed the app?"** section
8. **Enter your installation ID** and click "Save ID"

**Expected Result**: You should see:

```
✅ GitHub account linked
Installation ID: 12345678
```

### Step 2: Deploy a Kubernetes App

1. **Go to homepage**: http://localhost:3000
2. **Click on "Kubernetes & GitOps" tab** (should be default)
3. **Click "Deploy"** on any template
4. **Fill in the form**:
   - **App Name**: `my-test-app` (or anything you want)
   - **Project Name**: `test-project` (required for Kubernetes)
   - **Other fields**: Use the defaults or customize
5. **Click "Deploy"** button

### Step 3: Watch It Deploy

1. **Scroll down** to "All My Deployments" section
2. **Watch the status** change:
   - `pending` → `initializing` → `deploying` → `running`
3. **Check the backend logs** to see Terraform working in real-time

---

## 🔍 What Gets Created When You Deploy

When you deploy a Kubernetes app, the backend will:

1. ✅ Generate a GitHub token from your `installation_id`
2. ✅ Create a private GitHub repository
3. ✅ Push template code to the repo
4. ✅ Create a Kubernetes namespace
5. ✅ Create Vault secrets
6. ✅ Create an ArgoCD Application
7. ✅ Deploy your app!

---

## 📋 What the Frontend Now Sends

The frontend now sends this complete payload:

```json
{
  "name": "my-test-app",
  "template_id": "k3s-gitops-app",
  "project_id": "test-project",
  "app_config": {
    "template_repo_name": "template-html-css",
    "app_type": "static",
    "github_owner": "3-Istor",
    "project_name": "test-project",
    "github_installation_id": 12345678 // ← Automatically fetched!
  }
}
```

---

## 🐛 If It Still Fails

### Error: "Missing GitHub Integration"

**Cause**: You haven't linked your GitHub account yet
**Solution**: Go to Step 1 above

### Error: "No value for required variable"

**Cause**: The template has missing required fields
**Solution**: Make sure you filled in ALL required fields in the form (marked with \*)

### Error: "GitHub App authentication failed"

**Cause**: Your installation_id might be invalid
**Solution**:

1. Go to `/account` page
2. Try linking GitHub again
3. If already linked, check the backend logs for the actual installation_id

---

## 🔬 Debugging Commands

### Check if GitHub is linked:

```bash
# Check database directly
sqlite3 backend/app.db "SELECT * FROM user_github_installations;"
```

### Watch backend logs in real-time:

```bash
tail -f backend/logs/cmp.log | grep -E "(INFO|ERROR)"
```

### Check frontend console:

Open browser DevTools (F12) → Console tab → Look for any errors

---

## ✅ What's Fixed

1. ✅ **Backend validates** and shows clear errors
2. ✅ **Frontend fetches** `github_installation_id` automatically
3. ✅ **Frontend includes** `project_name` in the payload
4. ✅ **Deploy Modal** has a "Project Name" field for Kubernetes
5. ✅ **Terraform gets** all required variables

---

## 📁 Files That Were Modified

**Frontend** (just now):

- `frontend/src/app/page.tsx` - Fetches GitHub status and adds to payload
- `frontend/src/lib/api.ts` - Updated createDeployment type
- `frontend/src/components/catalog/DeployModal.tsx` - Added project_name field

**Backend** (already done):

- `backend/app/services/terraform_orchestrator.py` - Generates GitHub token
- `backend/app/routers/account.py` - Returns github_installation_id

---

## 🎯 Next Steps After Successful Deploy

Once your app is deployed:

1. **View it in the dashboard** - Status should show "running"
2. **Check the GitHub repo** - You'll see a new private repo created
3. **Check ArgoCD** - You'll see the app syncing
4. **Check Vault** - Your secrets are stored there

---

## 💡 Tips

- **Start simple**: Use a basic template first (e.g., static HTML)
- **Watch the logs**: The backend logs show every Terraform step
- **Be patient**: First deploy takes 2-3 minutes (GitHub, Terraform, ArgoCD)
- **Test destroy**: After deploying, try deleting the app - it should work now!

---

## 🆘 Still Stuck?

Run this command to verify everything:

```bash
cd backend
poetry run python -c "
from app.services.terraform_orchestrator import TerraformOrchestrator
print('✅ Orchestrator imports OK')
print('✅ Ready to deploy!')
"
```

If that works, you're good to go! Just follow Step 1 and Step 2 above.
