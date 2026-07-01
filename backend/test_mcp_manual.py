#!/usr/bin/env python3
"""
Interactive Manual Test for MCP Server

This script allows you to test MCP resources and tools manually
without needing Claude Desktop or understanding the MCP protocol.
"""

import asyncio
import sys
from pathlib import Path

# Colors
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
BLUE = '\033[0;34m'
CYAN = '\033[0;36m'
RED = '\033[0;31m'
NC = '\033[0m'

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

from app.mcp_server import (
    delete_deployment,
    deploy_new_app,
    get_deployment_status,
    get_docs_index,
    get_documentation,
    get_roadmap,
    list_active_deployments,
    list_projects,
)


def print_header(text):
    print(f"\n{BLUE}{'=' * 80}{NC}")
    print(f"{BLUE}{text:^80}{NC}")
    print(f"{BLUE}{'=' * 80}{NC}\n")


def print_section(text):
    print(f"\n{CYAN}{'─' * 80}{NC}")
    print(f"{CYAN}{text}{NC}")
    print(f"{CYAN}{'─' * 80}{NC}")


def print_success(text):
    print(f"{GREEN}✓ {text}{NC}")


def print_warning(text):
    print(f"{YELLOW}⚠ {text}{NC}")


def print_error(text):
    print(f"{RED}✗ {text}{NC}")


def print_result(result, max_lines=20):
    """Print result with optional truncation."""
    lines = result.split('\n')

    if len(lines) > max_lines:
        for line in lines[:max_lines]:
            print(f"  {line}")
        print(f"\n  {YELLOW}... ({len(lines) - max_lines} more lines){NC}")
    else:
        for line in lines:
            print(f"  {line}")


def test_resources():
    """Test MCP resources interactively."""
    print_header("🔍 Testing MCP Resources (Documentation)")

    print(f"{CYAN}Available resources:{NC}")
    print("  1. docs://index")
    print("  2. docs://roadmap")
    print("  3. docs://01-architecture/01-system-overview")
    print("  4. docs://02-core-components/05-github-integration")
    print("  5. docs://03-pipelines-and-workflows/01-app-provisioning-flow")
    print("  6. Custom query")
    print("  0. Back to main menu")

    while True:
        choice = input(f"\n{CYAN}Choose a resource (0-6): {NC}").strip()

        if choice == "0":
            break
        elif choice == "1":
            print_section("docs://index")
            result = get_docs_index()
            print_result(result, max_lines=30)
        elif choice == "2":
            print_section("docs://roadmap")
            result = get_roadmap()
            print_result(result, max_lines=30)
        elif choice == "3":
            print_section("docs://01-architecture/01-system-overview")
            result = get_documentation("01-architecture", "01-system-overview")
            print_result(result, max_lines=30)
        elif choice == "4":
            print_section("docs://02-core-components/05-github-integration")
            result = get_documentation("02-core-components", "05-github-integration")
            print_result(result, max_lines=30)
        elif choice == "5":
            print_section("docs://03-pipelines-and-workflows/01-app-provisioning-flow")
            result = get_documentation("03-pipelines-and-workflows", "01-app-provisioning-flow")
            print_result(result, max_lines=30)
        elif choice == "6":
            category = input(f"{CYAN}  Category (e.g., 01-architecture): {NC}").strip()
            filename = input(f"{CYAN}  Filename (without .md): {NC}").strip()
            print_section(f"docs://{category}/{filename}")
            result = get_documentation(category, filename)
            print_result(result, max_lines=30)
        else:
            print_error("Invalid choice")

        input(f"\n{YELLOW}Press Enter to continue...{NC}")


async def test_tools():
    """Test MCP tools interactively."""
    print_header("🛠️  Testing MCP Tools (API Actions)")

    print(f"{YELLOW}Note: These tools require a running backend and valid token{NC}")
    print(f"{YELLOW}      Backend should be at: http://localhost:8000/api{NC}\n")

    # Ask for token once
    token = input(f"{CYAN}Enter your bearer token (or 'fake' to test error handling): {NC}").strip()

    if not token:
        token = "fake-token"
        print_warning("Using fake token - will test error handling")

    while True:
        print(f"\n{CYAN}Available tools:{NC}")
        print("  1. list_active_deployments - List all deployments")
        print("  2. get_deployment_status - Get status of specific deployment")
        print("  3. list_projects - List all projects")
        print("  4. deploy_new_app - Create new Kubernetes app")
        print("  5. delete_deployment - Delete a deployment")
        print("  0. Back to main menu")

        choice = input(f"\n{CYAN}Choose a tool (0-5): {NC}").strip()

        if choice == "0":
            break

        elif choice == "1":
            print_section("list_active_deployments")
            print(f"Calling API with token: {token[:20]}...")
            result = await list_active_deployments(token)
            print_result(result, max_lines=50)

        elif choice == "2":
            print_section("get_deployment_status")
            deployment_id = input(f"{CYAN}  Deployment ID: {NC}").strip()
            if deployment_id.isdigit():
                result = await get_deployment_status(token, int(deployment_id))
                print_result(result, max_lines=50)
            else:
                print_error("Invalid deployment ID")

        elif choice == "3":
            print_section("list_projects")
            print(f"Calling API with token: {token[:20]}...")
            result = await list_projects(token)
            print_result(result, max_lines=50)

        elif choice == "4":
            print_section("deploy_new_app")
            print(f"\n{YELLOW}This will create a real deployment!{NC}\n")

            name = input(f"{CYAN}  App name: {NC}").strip()
            project = input(f"{CYAN}  Project name: {NC}").strip()
            template = input(f"{CYAN}  Template ID [kubernetes-fastapi]: {NC}").strip() or "kubernetes-fastapi"
            github_id = input(f"{CYAN}  GitHub installation ID [12345678]: {NC}").strip() or "12345678"
            replicas = input(f"{CYAN}  Replica count [2]: {NC}").strip() or "2"
            sso = input(f"{CYAN}  Enable SSO? (y/n) [n]: {NC}").strip().lower() == 'y'

            confirm = input(f"\n{YELLOW}Create deployment '{name}' in project '{project}'? (yes/no): {NC}").strip()

            if confirm.lower() == 'yes':
                result = await deploy_new_app(
                    token=token,
                    name=name,
                    project_name=project,
                    template_id=template,
                    github_installation_id=github_id,
                    replica_count=int(replicas),
                    sso_protected=sso
                )
                print_result(result)
            else:
                print_warning("Deployment cancelled")

        elif choice == "5":
            print_section("delete_deployment")
            print(f"\n{RED}⚠️  WARNING: This will delete a real deployment!{NC}\n")

            deployment_id = input(f"{CYAN}  Deployment ID to delete: {NC}").strip()

            if deployment_id.isdigit():
                confirm = input(f"\n{RED}Delete deployment {deployment_id}? (yes/no): {NC}").strip()

                if confirm.lower() == 'yes':
                    result = await delete_deployment(token, int(deployment_id))
                    print_result(result)
                else:
                    print_warning("Deletion cancelled")
            else:
                print_error("Invalid deployment ID")

        else:
            print_error("Invalid choice")

        input(f"\n{YELLOW}Press Enter to continue...{NC}")


async def main():
    """Main interactive menu."""
    print("\n")
    print(f"{GREEN}╔{'═' * 78}╗{NC}")
    print(f"{GREEN}║{' ' * 20}CNP MCP Manual Testing Tool{' ' * 29}║{NC}")
    print(f"{GREEN}╚{'═' * 78}╝{NC}")

    print(f"\n{CYAN}This tool lets you test MCP functionality manually.{NC}")
    print(f"{CYAN}You can test documentation resources and API tools.{NC}\n")

    while True:
        print(f"\n{BLUE}{'─' * 80}{NC}")
        print(f"{CYAN}Main Menu:{NC}")
        print("  1. Test MCP Resources (Documentation)")
        print("  2. Test MCP Tools (API Actions)")
        print("  3. Show Available Documentation")
        print("  4. Quick Demo")
        print("  0. Exit")
        print(f"{BLUE}{'─' * 80}{NC}")

        choice = input(f"\n{CYAN}Choose an option (0-4): {NC}").strip()

        if choice == "0":
            print(f"\n{GREEN}👋 Goodbye!{NC}\n")
            break

        elif choice == "1":
            test_resources()

        elif choice == "2":
            await test_tools()

        elif choice == "3":
            print_header("📚 Available Documentation")
            docs_dir = Path(__file__).parent.parent / ".kiro" / "steering" / "docs"

            if docs_dir.exists():
                categories = sorted([d for d in docs_dir.iterdir() if d.is_dir() and not d.name.startswith(".")])

                for category in categories:
                    print(f"\n{CYAN}{category.name}/{NC}")
                    md_files = sorted(category.glob("*.md"))
                    for md_file in md_files:
                        print(f"  • {md_file.stem}")

                print(f"\n{GREEN}Total: {sum(len(list(c.glob('*.md'))) for c in categories)} files in {len(categories)} categories{NC}")
            else:
                print_error(f"Documentation not found: {docs_dir}")

            input(f"\n{YELLOW}Press Enter to continue...{NC}")

        elif choice == "4":
            print_header("🎬 Quick Demo")

            print(f"{CYAN}1. Reading main documentation index...{NC}")
            result = get_docs_index()
            print_result(result, max_lines=10)

            print(f"\n{CYAN}2. Reading system overview...{NC}")
            result = get_documentation("01-architecture", "01-system-overview")
            print_result(result, max_lines=10)

            print(f"\n{CYAN}3. Testing API tool (list_active_deployments)...{NC}")
            print(f"{YELLOW}   Note: Will fail if backend not running - this is expected{NC}")
            result = await list_active_deployments("demo-token")
            print_result(result, max_lines=5)

            print(f"\n{GREEN}✓ Demo complete!{NC}")
            input(f"\n{YELLOW}Press Enter to continue...{NC}")

        else:
            print_error("Invalid choice")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}Interrupted by user{NC}")
        print(f"{GREEN}👋 Goodbye!{NC}\n")
        sys.exit(0)
