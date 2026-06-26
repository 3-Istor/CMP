# 🚀 CNP MCP Quick Start - 5 Minutes

Get the MCP server running in 5 minutes!

---

## Step 1: Install (1 min)

```bash
cd backend
./setup_mcp.sh
```

Or manually:

```bash
poetry install
poetry run python test_mcp_server.py
```

**Expected Output:**

```
✓ Resources: Documentation access working
✓ Tools: API interface working
```

---

## Step 2: Test Swagger UI (1 min)

```bash
# Terminal 1: Start backend
poetry run uvicorn app.main:app --reload
```

Visit: http://localhost:8000/docs

1. Click **"Authorize"** button
2. Complete Keycloak login
3. Try any endpoint (e.g., `GET /api/deployments`)

**You should see:** Interactive API docs with OAuth2 authentication! 🎉

---

## Step 3: Configure Claude Desktop (2 min)

### Linux/Mac:

```bash
# Copy generated config
cp claude_desktop_config.json ~/.config/Claude/claude_desktop_config.json

# Or manually edit
nano ~/.config/Claude/claude_desktop_config.json
```

### Add this:

```json
{
  "mcpServers": {
    "cnp-portal": {
      "command": "poetry",
      "args": ["run", "python", "app/mcp_server.py"],
      "cwd": "/home/brian/Documents/aepita/ing2/3-Istor/CMP/backend",
      "env": {
        "CMP_API_URL": "http://localhost:8000/api"
      }
    }
  }
}
```

**Important:** Change `cwd` to your actual backend path!

---

## Step 4: Test in Claude (1 min)

1. Restart Claude Desktop
2. Look for 🔌 icon (MCP connected)
3. Ask Claude:

```
"Read docs://01-architecture/01-system-overview and summarize the CNP architecture"
```

**Expected:** Claude reads your documentation and explains it! 🤖

---

## Example Workflows

### Read Documentation

```
"Explain how GitHub authentication works in CNP"
→ Claude calls docs://02-core-components/05-github-integration
```

### List Deployments

```
"Show all my deployments. Token: eyJhbG..."
→ Claude calls list_active_deployments(token)
```

### Deploy App

```
"Deploy a FastAPI app called 'test-api' in project 'alpha'"
→ Claude calls deploy_new_app(name="test-api", project_name="alpha")
```

---

## Troubleshooting

### MCP Not Connecting?

**Check logs:**

```bash
# Linux
tail -f ~/.config/Claude/logs/mcp*.log

# Mac
tail -f ~/Library/Logs/Claude/mcp*.log
```

**Test manually:**

```bash
poetry run python app/mcp_server.py
# Should print startup info
```

**Verify config:**

```bash
cat ~/.config/Claude/claude_desktop_config.json | jq .
# Should be valid JSON
```

### Backend Not Starting?

```bash
# Check imports
poetry run python -c "from app.main import app; print('OK')"

# Check logs
tail -f logs/app.log
```

### Docs Not Found?

```bash
# Verify documentation exists
ls -la ../.kiro/steering/docs/
```

---

## What You Get

### ✅ OpenAPI/Swagger UI

- Interactive API testing
- Real Keycloak authentication
- No Postman needed
- Visit: http://localhost:8000/docs

### ✅ MCP Server

- AI reads your docs
- AI calls your APIs
- Natural language workflows
- Works in Claude & Cursor

### ✅ Documentation

- `MCP_SERVER_README.md` - Full guide
- `MCP_IMPLEMENTATION_SUMMARY.md` - Details
- `.kiro/steering/docs/05-cmp-backend-api/11-cmp-mcp-integration.md` - Architecture

---

## Next Steps

### Try These Prompts:

1. **Documentation Query:**

   ```
   "Read the roadmap and tell me what Phase 4 includes"
   ```

2. **API Query:**

   ```
   "List all projects (my token is eyJhbG...)"
   ```

3. **Deployment:**

   ```
   "Create a new app called 'billing-web' in project 'finance' with 3 replicas"
   ```

4. **Combined:**
   ```
   "Read the deployment API docs, then show me all my deployments"
   ```

---

## Architecture

```
┌──────────────────┐
│ Claude Desktop   │  ← You type natural language prompts
└────────┬─────────┘
         │ MCP Protocol (stdio)
         ▼
┌──────────────────┐
│ MCP Server       │  ← Reads docs + calls APIs
│ (Python)         │
└────┬───────┬─────┘
     │       │
     │       └─────► 📁 Documentation (.kiro/steering/docs/)
     │
     └─────────────► 🌐 CMP Backend API (http://localhost:8000/api)
                            │
                            ├─► 🔒 Keycloak (auth.3istor.com)
                            ├─► 🐙 GitHub (api.github.com)
                            └─► ☸️ Kubernetes (ArgoCD, Vault)
```

---

## Status Dashboard

| Component      | Status       | Test                                   |
| -------------- | ------------ | -------------------------------------- |
| Dependencies   | ✅ Installed | `poetry install`                       |
| MCP Server     | ✅ Working   | `poetry run python test_mcp_server.py` |
| OpenAPI OAuth2 | ✅ Working   | Visit http://localhost:8000/docs       |
| Documentation  | ✅ 25+ files | `ls ../.kiro/steering/docs/`           |
| Test Script    | ✅ Passing   | All resources + tools tested           |
| Setup Script   | ✅ Ready     | `./setup_mcp.sh`                       |

---

## Support

- 📖 **Full Guide:** `MCP_SERVER_README.md`
- 📝 **Summary:** `MCP_IMPLEMENTATION_SUMMARY.md`
- 🏗️ **Architecture:** `.kiro/steering/docs/05-cmp-backend-api/11-cmp-mcp-integration.md`
- 🧪 **Tests:** `test_mcp_server.py`

---

**That's it! You're ready to use AI-assisted platform management! 🎉**
