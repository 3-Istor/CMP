#!/usr/bin/env python3
"""
CNP Portal MCP Server

This Model Context Protocol (MCP) server exposes CNP platform documentation
and deployment APIs to AI coding assistants (Cursor, Claude Desktop, etc.).

Resources:
  - Documentation from .kiro/steering/docs/

Tools:
  - list_active_deployments: Query current deployments
  - deploy_new_app: Trigger Day-0 Kubernetes provisioning
  - get_deployment_status: Check deployment health
  - list_projects: List available projects
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# ══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════

# Initialize FastMCP Server
mcp = FastMCP("CNP Portal")

# Resolve paths
BASE_DIR = Path(__file__).parent.parent.parent
DOCS_DIR = BASE_DIR / ".kiro" / "steering" / "docs"
CMP_API_URL = os.getenv("CMP_API_URL", "http://localhost:8000/api")

# Timeout for API calls
API_TIMEOUT = 30.0


# ══════════════════════════════════════════════════════════════════════════
# MCP RESOURCES (Read-only documentation)
# ══════════════════════════════════════════════════════════════════════════

@mcp.resource("docs://index")
def get_docs_index() -> str:
    """Get the main documentation index with all available documents."""
    readme_path = DOCS_DIR / "README.md"

    if not readme_path.exists():
        return "Error: Documentation index not found."

    with open(readme_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Add available categories
    categories = []
    for item in DOCS_DIR.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            categories.append(item.name)

    footer = f"\n\n## Available Categories\n\n"
    footer += "\n".join(f"- `{cat}/`" for cat in sorted(categories))

    return content + footer


@mcp.resource("docs://{category}/{filename}")
def get_documentation(category: str, filename: str) -> str:
    """
    Retrieve specific architectural documentation files.

    Examples:
      - docs://01-architecture/01-system-overview
      - docs://02-core-components/05-github-integration
      - docs://03-pipelines-and-workflows/01-app-provisioning-flow
    """
    # Add .md extension if not present
    if not filename.endswith(".md"):
        filename = f"{filename}.md"

    file_path = DOCS_DIR / category / filename

    if not file_path.exists():
        # Try to list available files in category
        category_path = DOCS_DIR / category
        if category_path.exists():
            available = [f.stem for f in category_path.glob("*.md")]
            return (
                f"Error: Document '{filename}' not found in '{category}'.\n\n"
                f"Available files: {', '.join(available)}"
            )
        return f"Error: Category '{category}' not found."

    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


@mcp.resource("docs://roadmap")
def get_roadmap() -> str:
    """Get the implementation roadmap showing project phases."""
    roadmap_path = DOCS_DIR / "README_ROADMAP.md"

    if not roadmap_path.exists():
        return "Error: Roadmap not found."

    with open(roadmap_path, "r", encoding="utf-8") as f:
        return f.read()


# ══════════════════════════════════════════════════════════════════════════
# MCP TOOLS (API actions)
# ══════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def list_active_deployments(token: str) -> str:
    """
    List all current application deployments registered in the portal.

    Args:
        token: Bearer token for authentication (from Keycloak OIDC)

    Returns:
        JSON string with deployment list or error message
    """
    headers = {"Authorization": f"Bearer {token}"}

    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            response = await client.get(
                f"{CMP_API_URL}/deployments",
                headers=headers
            )

            if response.status_code == 401:
                return "Error: Authentication failed. Token may be expired."

            if response.status_code != 200:
                return f"Error: Failed to fetch deployments (HTTP {response.status_code})"

            data = response.json()

            # Format response
            result = {
                "total": len(data),
                "deployments": [
                    {
                        "id": d["id"],
                        "name": d["name"],
                        "status": d["status"],
                        "provider_type": d.get("provider_type", "legacy_hybrid"),
                        "project_id": d.get("project_id"),
                        "github_repo_url": d.get("github_repo_url"),
                        "argocd_app_name": d.get("argocd_app_name"),
                    }
                    for d in data
                ]
            }

            return json.dumps(result, indent=2)

    except httpx.TimeoutException:
        return "Error: Request timed out. CMP API may be unavailable."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
async def get_deployment_status(token: str, deployment_id: int) -> str:
    """
    Get detailed status of a specific deployment.

    Args:
        token: Bearer token for authentication
        deployment_id: ID of the deployment to query

    Returns:
        JSON string with deployment details
    """
    headers = {"Authorization": f"Bearer {token}"}

    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            response = await client.get(
                f"{CMP_API_URL}/deployments/{deployment_id}",
                headers=headers
            )

            if response.status_code == 404:
                return f"Error: Deployment {deployment_id} not found."

            if response.status_code != 200:
                return f"Error: Failed to fetch deployment (HTTP {response.status_code})"

            return response.text

    except httpx.TimeoutException:
        return "Error: Request timed out."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
async def list_projects(token: str) -> str:
    """
    List all available projects.

    Args:
        token: Bearer token for authentication

    Returns:
        JSON string with project list
    """
    headers = {"Authorization": f"Bearer {token}"}

    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            response = await client.get(
                f"{CMP_API_URL}/projects",
                headers=headers
            )

            if response.status_code != 200:
                return f"Error: Failed to fetch projects (HTTP {response.status_code})"

            return response.text

    except httpx.TimeoutException:
        return "Error: Request timed out."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
async def deploy_new_app(
    token: str,
    name: str,
    project_name: str,
    template_id: str = "kubernetes-fastapi",
    github_installation_id: str = "12345678",
    replica_count: int = 2,
    sso_protected: bool = False
) -> str:
    """
    Trigger Day-0 provisioning of a new Kubernetes GitOps application.

    Args:
        token: Bearer token for authentication
        name: Application name (e.g., 'billing-web')
        project_name: Project/team name (e.g., 'project-alpha')
        template_id: Template to use (default: 'kubernetes-fastapi')
        github_installation_id: GitHub App installation ID
        replica_count: Number of pod replicas (1-10)
        sso_protected: Enable Keycloak SSO protection

    Returns:
        JSON string with deployment creation result
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload = {
        "name": name,
        "template_id": template_id,
        "provider_type": "kubernetes",
        "app_config": {
            "project_name": project_name,
            "github_installation_id": github_installation_id,
            "replica_count": replica_count,
            "sso_protected": sso_protected
        }
    }

    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            response = await client.post(
                f"{CMP_API_URL}/deployments",
                headers=headers,
                json=payload
            )

            if response.status_code == 400:
                return f"Error: Invalid request. {response.text}"

            if response.status_code == 401:
                return "Error: Authentication failed."

            if response.status_code != 201:
                return f"Error: Failed to create deployment (HTTP {response.status_code}): {response.text}"

            data = response.json()
            return json.dumps({
                "status": "success",
                "message": f"Deployment '{name}' created successfully",
                "deployment_id": data.get("id"),
                "deployment_status": data.get("status"),
                "next_steps": [
                    "Monitor deployment status with get_deployment_status",
                    f"Check GitHub repo once status is 'running'",
                    f"View in ArgoCD: https://argocd.3istor.com"
                ]
            }, indent=2)

    except httpx.TimeoutException:
        return "Error: Request timed out. Deployment may still be processing."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
async def delete_deployment(token: str, deployment_id: int) -> str:
    """
    Delete a deployment and all associated resources.

    Warning: This action is irreversible!

    Args:
        token: Bearer token for authentication
        deployment_id: ID of the deployment to delete

    Returns:
        JSON string with deletion result
    """
    headers = {"Authorization": f"Bearer {token}"}

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:  # Longer timeout for deletion
            response = await client.delete(
                f"{CMP_API_URL}/deployments/{deployment_id}",
                headers=headers
            )

            if response.status_code == 404:
                return f"Error: Deployment {deployment_id} not found."

            if response.status_code != 200:
                return f"Error: Failed to delete deployment (HTTP {response.status_code})"

            return json.dumps({
                "status": "success",
                "message": f"Deployment {deployment_id} deletion initiated",
                "note": "Terraform destroy is running in background"
            }, indent=2)

    except httpx.TimeoutException:
        return "Error: Request timed out. Deletion may still be processing."
    except Exception as e:
        return f"Error: {str(e)}"


# ══════════════════════════════════════════════════════════════════════════
# SERVER ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Verify documentation directory exists
    if not DOCS_DIR.exists():
        print(f"Warning: Documentation directory not found: {DOCS_DIR}")
        print("MCP resources will return errors.")

    print(f"🚀 Starting CNP Portal MCP Server")
    print(f"📚 Documentation path: {DOCS_DIR}")
    print(f"🔌 CMP API URL: {CMP_API_URL}")
    print(f"")
    print(f"Available resources:")
    print(f"  - docs://index")
    print(f"  - docs://roadmap")
    print(f"  - docs://<category>/<filename>")
    print(f"")
    print(f"Available tools:")
    print(f"  - list_active_deployments")
    print(f"  - get_deployment_status")
    print(f"  - list_projects")
    print(f"  - deploy_new_app")
    print(f"  - delete_deployment")
    print(f"")

    # Run MCP server over stdio
    mcp.run()
