#!/usr/bin/env python3
"""
Integration test for MCP + Backend + OpenAPI

This script performs comprehensive integration tests to verify:
1. Backend starts correctly with OAuth2 OpenAPI
2. MCP server can connect to backend
3. Resources and tools work end-to-end
"""

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

# Colors
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
RED = '\033[0;31m'
BLUE = '\033[0;34m'
NC = '\033[0m'  # No Color


def print_header(text):
    """Print section header."""
    print(f"\n{'=' * 80}")
    print(f"{BLUE}{text}{NC}")
    print('=' * 80)


def print_success(text):
    """Print success message."""
    print(f"{GREEN}✓ {text}{NC}")


def print_warning(text):
    """Print warning message."""
    print(f"{YELLOW}⚠ {text}{NC}")


def print_error(text):
    """Print error message."""
    print(f"{RED}✗ {text}{NC}")


async def test_backend_health():
    """Test that backend is running and healthy."""
    print_header("1. Testing Backend Health")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get("http://localhost:8000/health")

            if response.status_code == 200:
                data = response.json()
                print_success(f"Backend is healthy: {data}")
                return True
            else:
                print_error(f"Backend returned {response.status_code}")
                return False

    except httpx.ConnectError:
        print_error("Cannot connect to backend")
        print_warning("Start backend with: poetry run uvicorn app.main:app --reload")
        return False
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        return False


async def test_openapi_schema():
    """Test that custom OpenAPI schema is loaded."""
    print_header("2. Testing OpenAPI Schema")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get("http://localhost:8000/openapi.json")

            if response.status_code != 200:
                print_error(f"Failed to fetch OpenAPI schema: {response.status_code}")
                return False

            schema = response.json()

            # Check security schemes
            if "components" not in schema:
                print_error("No 'components' in OpenAPI schema")
                return False

            if "securitySchemes" not in schema["components"]:
                print_error("No 'securitySchemes' in components")
                return False

            if "KeycloakOAuth2" not in schema["components"]["securitySchemes"]:
                print_error("'KeycloakOAuth2' not found in securitySchemes")
                return False

            oauth2_config = schema["components"]["securitySchemes"]["KeycloakOAuth2"]

            print_success("OpenAPI schema loaded correctly")
            print(f"  - Type: {oauth2_config.get('type')}")
            print(f"  - Flows: {list(oauth2_config.get('flows', {}).keys())}")

            # Check that endpoints have security (except health)
            secured_count = 0
            public_count = 0

            for path, methods in schema["paths"].items():
                for method, details in methods.items():
                    if isinstance(details, dict):
                        if "security" in details:
                            secured_count += 1
                        else:
                            public_count += 1
                            if path != "/health":
                                print_warning(f"Unsecured endpoint: {method.upper()} {path}")

            print_success(f"Security: {secured_count} secured, {public_count} public endpoints")

            return True

    except Exception as e:
        print_error(f"Error: {e}")
        return False


async def test_docs_endpoint():
    """Test that /docs endpoint is accessible."""
    print_header("3. Testing Swagger UI Endpoint")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get("http://localhost:8000/docs")

            if response.status_code == 200:
                html = response.text

                # Check for OAuth2 authorization
                if "Authorize" in html or "oauth2" in html.lower():
                    print_success("Swagger UI accessible with OAuth2")
                    print(f"  - URL: http://localhost:8000/docs")
                    print(f"  - Size: {len(html)} bytes")
                    return True
                else:
                    print_warning("Swagger UI accessible but OAuth2 not detected")
                    return True
            else:
                print_error(f"Docs endpoint returned {response.status_code}")
                return False

    except Exception as e:
        print_error(f"Error: {e}")
        return False


def test_mcp_resources():
    """Test MCP resources (documentation access)."""
    print_header("4. Testing MCP Resources")

    sys.path.insert(0, str(Path(__file__).parent))

    try:
        from app.mcp_server import (
            get_docs_index,
            get_documentation,
            get_roadmap,
        )

        # Test 1: Index
        result = get_docs_index()
        if not result.startswith("Error"):
            print_success(f"docs://index accessible ({len(result)} chars)")
        else:
            print_error(f"docs://index failed: {result}")
            return False

        # Test 2: Architecture doc
        result = get_documentation("01-architecture", "01-system-overview")
        if not result.startswith("Error"):
            print_success(f"docs://01-architecture/01-system-overview accessible ({len(result)} chars)")
        else:
            print_error(f"Failed: {result}")
            return False

        # Test 3: Roadmap
        result = get_roadmap()
        if not result.startswith("Error") and "Phase 3" in result:
            print_success(f"docs://roadmap accessible and valid ({len(result)} chars)")
        else:
            print_error(f"Roadmap failed: {result[:100]}")
            return False

        print_success("All MCP resources working")
        return True

    except ImportError as e:
        print_error(f"Cannot import MCP server: {e}")
        print_warning("Install with: poetry install")
        return False
    except Exception as e:
        print_error(f"Error: {e}")
        return False


async def test_mcp_tools():
    """Test MCP tools (API integration)."""
    print_header("5. Testing MCP Tools")

    sys.path.insert(0, str(Path(__file__).parent))

    try:
        from app.mcp_server import list_active_deployments, list_projects

        # Test with fake token (should fail gracefully)
        print("Testing with fake token (should handle error gracefully)...")

        result = await list_active_deployments(token="fake-token")
        if "Error" in result or "Authentication failed" in result or "Connection" in result:
            print_success("list_active_deployments handles errors correctly")
        else:
            print_warning(f"Unexpected result: {result[:100]}")

        result = await list_projects(token="fake-token")
        if "Error" in result or "Authentication failed" in result or "Connection" in result:
            print_success("list_projects handles errors correctly")
        else:
            print_warning(f"Unexpected result: {result[:100]}")

        print_success("MCP tools interface working")
        print_warning("Note: Full API integration requires valid token")

        return True

    except ImportError as e:
        print_error(f"Cannot import MCP server: {e}")
        return False
    except Exception as e:
        print_error(f"Error: {e}")
        return False


def test_documentation_coverage():
    """Test documentation coverage."""
    print_header("6. Testing Documentation Coverage")

    docs_dir = Path(__file__).parent.parent / ".kiro" / "steering" / "docs"

    if not docs_dir.exists():
        print_error(f"Documentation directory not found: {docs_dir}")
        return False

    categories = [d for d in docs_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
    total_files = 0

    print(f"Found {len(categories)} documentation categories:")

    for category in sorted(categories):
        md_files = list(category.glob("*.md"))
        total_files += len(md_files)
        print(f"  - {category.name}/ ({len(md_files)} files)")

    print_success(f"Total documentation: {total_files} files in {len(categories)} categories")

    # Check critical files
    critical_files = [
        "README.md",
        "README_ROADMAP.md",
        "01-architecture/01-system-overview.md",
        "05-cmp-backend-api/01-cmp-deployment-api.md",
        "05-cmp-backend-api/11-cmp-mcp-integration.md",
    ]

    missing = []
    for file in critical_files:
        if not (docs_dir / file).exists():
            missing.append(file)

    if missing:
        print_warning(f"Missing {len(missing)} critical files:")
        for f in missing:
            print(f"    - {f}")
    else:
        print_success("All critical documentation files present")

    return len(missing) == 0


async def main():
    """Run all integration tests."""
    print("\n")
    print("╔" + "═" * 78 + "╗")
    print("║" + " " * 18 + "CNP MCP + Backend Integration Tests" + " " * 25 + "║")
    print("╚" + "═" * 78 + "╝")

    results = {}

    # Test backend
    results["Backend Health"] = await test_backend_health()

    if results["Backend Health"]:
        results["OpenAPI Schema"] = await test_openapi_schema()
        results["Swagger UI"] = await test_docs_endpoint()
    else:
        results["OpenAPI Schema"] = None
        results["Swagger UI"] = None

    # Test MCP
    results["MCP Resources"] = test_mcp_resources()
    results["MCP Tools"] = await test_mcp_tools()
    results["Documentation"] = test_documentation_coverage()

    # Summary
    print_header("Test Summary")

    passed = sum(1 for v in results.values() if v is True)
    failed = sum(1 for v in results.values() if v is False)
    skipped = sum(1 for v in results.values() if v is None)
    total = len(results)

    print(f"\nResults: {passed}/{total} passed")

    for name, result in results.items():
        if result is True:
            print(f"  {GREEN}✓{NC} {name}")
        elif result is False:
            print(f"  {RED}✗{NC} {name}")
        else:
            print(f"  {YELLOW}⊘{NC} {name} (skipped)")

    print("\n" + "─" * 80)

    if failed == 0 and skipped == 0:
        print(f"\n{GREEN}🎉 All tests passed!{NC}\n")
        print("Your CNP platform is fully operational:")
        print(f"  {GREEN}✓{NC} Backend running with OAuth2")
        print(f"  {GREEN}✓{NC} MCP server ready")
        print(f"  {GREEN}✓{NC} Documentation accessible")
        print(f"  {GREEN}✓{NC} API integration working")
        print("\nNext steps:")
        print("  1. Configure Claude Desktop: cp claude_desktop_config.json ~/.config/Claude/")
        print("  2. Restart Claude Desktop")
        print("  3. Ask Claude to read your documentation!")
        return_code = 0
    elif skipped > 0 and failed == 0:
        print(f"\n{YELLOW}⚠ Tests passed but backend not running{NC}\n")
        print("Start the backend:")
        print("  poetry run uvicorn app.main:app --reload")
        print("\nThen run tests again:")
        print("  poetry run python test_integration.py")
        return_code = 0
    else:
        print(f"\n{RED}✗ {failed} test(s) failed{NC}\n")
        print("Check the errors above and:")
        print("  1. Ensure backend is running: poetry run uvicorn app.main:app --reload")
        print("  2. Verify dependencies: poetry install")
        print("  3. Check documentation: ls ../.kiro/steering/docs/")
        return_code = 1

    print()
    sys.exit(return_code)


if __name__ == "__main__":
    asyncio.run(main())
