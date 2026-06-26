# CNP MCP Server Setup Guide

This guide explains how to configure and use the CNP Model Context Protocol (MCP) server with AI coding assistants.

## What is MCP?

The Model Context Protocol (MCP) allows AI assistants (Claude Desktop, Cursor, etc.) to:

- **Read** your platform documentation dynamically
- **Execute** API calls to list/create/delete deployments
- **Understand** your architecture in real-time

## Quick Start

### 1. Install Dependencies

```bash
cd backend
poetry add mcp
poetry install
```

### 2. Test the MCP Server

```bash
poetry run python test_mcp_server.py
```

This will verify:

- ✅ Documentation paths are accessible
- ✅ Resources work correctly
- ✅ Tools interface is functional

### 3. Configure in Claude Desktop

**Location**: `~/.config/Claude/claude_desktop_config.json` (Linux/Mac)

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

**Or copy the example config:**

```bash
# Linux/Mac
mkdir -p ~/.config/Claude
cp mcp-config-example.json ~/.config/Claude/claude_desktop_config.json

# Edit the 'cwd' path to match your system
nano ~/.config/Claude/claude_desktop_config.json
```

### 4. Restart Claude Desktop

Close and reopen Claude Desktop. You should see a 🔌 icon indicating the MCP server is connected.

## Available Resources

MCP Resources are read-only documentation:

```
docs://index                                    # Main documentation index
docs://roadmap                                  # Implementation roadmap
docs://01-architecture/01-system-overview       # System architecture
docs://02-core-components/05-github-integration # GitHub integration
docs://03-pipelines-and-workflows/01-app-provisioning-flow
docs://04-templates/02-helm-generic-chart
docs://05-cmp-backend-api/01-cmp-deployment-api
```

## Available Tools

MCP Tools are API actions:

### list_active_deployments

```python
List all deployments in the platform
Requires: Bearer token
```

### get_deployment_status

```python
Get detailed status of a specific deployment
Args: token, deployment_id
```

### list_projects

```python
List all available projects
Requires: Bearer token
```

### deploy_new_app

```python
Create a new Kubernetes GitOps application
Args:
  - token: Bearer token
  - name: App name
  - project_name: Project name
  - template_id: Template (default: kubernetes-fastapi)
  - github_installation_id: GitHub App ID
  - replica_count: Replicas (1-10)
  - sso_protected: Enable SSO (bool)
```

### delete_deployment

```python
Delete a deployment (irreversible!)
Args: token, deployment_id
```

## Usage Examples

### Example 1: Read Documentation

Ask Claude:

> "Read the documentation on GitHub integration and explain how the CNP Portal authenticates with GitHub."

Claude will call:

```
Resource: docs://02-core-components/05-github-integration
```

### Example 2: List Deployments

Ask Claude:

> "Show me all active deployments in the platform. My token is eyJhbG..."

Claude will call:

```python
Tool: list_active_deployments(token="eyJhbG...")
```

### Example 3: Deploy New App

Ask Claude:

> "Deploy a new FastAPI app called 'billing-api' in project 'finance-team'. Use 3 replicas and enable SSO."

Claude will:

1. Call `list_projects()` to verify 'finance-team' exists
2. Call `deploy_new_app(name="billing-api", project_name="finance-team", replica_count=3, sso_protected=True)`
3. Poll `get_deployment_status()` to monitor progress

## Getting a Bearer Token

### Option 1: From Swagger UI

1. Start backend: `poetry run uvicorn app.main:app --reload`
2. Visit: http://localhost:8000/docs
3. Click "Authorize"
4. Complete Keycloak login
5. Copy the token from browser DevTools (Network tab, Authorization header)

### Option 2: Using curl

```bash
# Get token from Keycloak
curl -X POST https://auth.3istor.com/realms/3istor/protocol/openid-connect/token \
  -d "client_id=cmp-backend" \
  -d "client_secret=YOUR_SECRET" \
  -d "grant_type=client_credentials" \
  | jq -r '.access_token'
```

## Troubleshooting

### MCP Server Not Connecting

1. Check Claude Desktop logs:
   - Mac: `~/Library/Logs/Claude/mcp*.log`
   - Linux: `~/.config/Claude/logs/mcp*.log`

2. Test manually:

   ```bash
   cd backend
   poetry run python app/mcp_server.py
   # Should print startup info without errors
   ```

3. Verify paths in config:
   ```bash
   cat ~/.config/Claude/claude_desktop_config.json
   # 'cwd' must be absolute path to backend/
   ```

### Documentation Not Found

```bash
# Verify docs exist
ls -la .kiro/steering/docs/

# Should show folders like:
# 01-architecture/
# 02-core-components/
# etc.
```

### API Calls Failing

1. Check backend is running:

   ```bash
   curl http://localhost:8000/health
   ```

2. Verify token is valid:

   ```bash
   curl -H "Authorization: Bearer YOUR_TOKEN" \
        http://localhost:8000/api/deployments
   ```

3. Check MCP server logs in Claude Desktop

## Advanced Configuration

### Custom API URL

If your backend runs on a different port/host:

```json
{
  "mcpServers": {
    "cnp-portal": {
      "env": {
        "CMP_API_URL": "https://cmp.3istor.com/api"
      }
    }
  }
}
```

### Enable Debug Logging

```json
{
  "mcpServers": {
    "cnp-portal": {
      "env": {
        "PYTHONUNBUFFERED": "1",
        "LOG_LEVEL": "DEBUG"
      }
    }
  }
}
```

## Architecture

```
┌─────────────────────┐
│  Claude Desktop /   │
│  Cursor IDE         │
└──────────┬──────────┘
           │ stdio
           ▼
┌─────────────────────┐
│  MCP Server         │
│  (app/mcp_server.py)│
├─────────────────────┤
│ Resources:          │
│  - Documentation    │
│                     │
│ Tools:              │
│  - API calls        │
└──────────┬──────────┘
           │
           ├──► File System (.kiro/steering/docs/)
           │
           └──► HTTP (CMP Backend API)
```

## Security Notes

- **Tokens**: MCP server uses your token but doesn't store it
- **Local Only**: MCP runs locally, no data sent to external servers
- **Read-only Docs**: Documentation resources are read-only
- **API Auth**: All API tools require valid bearer tokens

## Related Documentation

- Full guide: `.kiro/steering/docs/05-cmp-backend-api/11-cmp-mcp-integration.md`
- API spec: `.kiro/steering/docs/05-cmp-backend-api/01-cmp-deployment-api.md`
- OpenAPI docs: http://localhost:8000/docs (when backend is running)

## Support

Questions? Check:

1. Test output: `poetry run python test_mcp_server.py`
2. Claude Desktop logs
3. Backend logs: `backend/logs/app.log`
