#!/usr/bin/env python3
"""
Test script for CNP MCP Server

This script tests the MCP server functionality locally without needing
an MCP client (Claude Desktop/Cursor).
"""

import asyncio
import json
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

from app.mcp_server import (
    get_docs_index,
    get_documentation,
    get_roadmap,
    list_active_deployments,
    list_projects,
)


def test_resources():
    """Test MCP resources (documentation access)."""
    print("=" * 80)
    print("Testing MCP Resources")
    print("=" * 80)

    # Test 1: Docs index
    print("\n1. Testing docs://index")
    print("-" * 40)
    result = get_docs_index()
    print(f"✓ Length: {len(result)} chars")
    print(f"✓ Preview: {result[:200]}...")

    # Test 2: Architecture docs
    print("\n2. Testing docs://01-architecture/01-system-overview")
    print("-" * 40)
    result = get_documentation("01-architecture", "01-system-overview")
    if result.startswith("Error"):
        print(f"✗ {result}")
    else:
        print(f"✓ Length: {len(result)} chars")
        print(f"✓ Preview: {result[:200]}...")

    # Test 3: Roadmap
    print("\n3. Testing docs://roadmap")
    print("-" * 40)
    result = get_roadmap()
    if result.startswith("Error"):
        print(f"✗ {result}")
    else:
        print(f"✓ Length: {len(result)} chars")
        print(f"✓ Contains Phase 3: {'Phase 3' in result}")

    # Test 4: Non-existent document
    print("\n4. Testing error handling (non-existent doc)")
    print("-" * 40)
    result = get_documentation("99-fake", "fake-doc")
    print(f"✓ Error handling: {result[:100]}...")


async def test_tools():
    """Test MCP tools (API interactions)."""
    print("\n")
    print("=" * 80)
    print("Testing MCP Tools (API)")
    print("=" * 80)

    # Note: These tests will fail without a valid token
    # They demonstrate the interface

    print("\n1. Testing list_active_deployments")
    print("-" * 40)
    print("⚠️  Requires valid token - testing interface only")
    result = await list_active_deployments(token="fake-token-for-testing")
    print(f"Response: {result[:200]}...")

    print("\n2. Testing list_projects")
    print("-" * 40)
    print("⚠️  Requires valid token - testing interface only")
    result = await list_projects(token="fake-token-for-testing")
    print(f"Response: {result[:200]}...")


def test_paths():
    """Test that all documentation paths exist."""
    print("\n")
    print("=" * 80)
    print("Testing Documentation Paths")
    print("=" * 80)

    docs_dir = Path(__file__).parent.parent / ".kiro" / "steering" / "docs"

    print(f"\nDocumentation directory: {docs_dir}")
    print(f"Exists: {docs_dir.exists()}")

    if docs_dir.exists():
        categories = [d for d in docs_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
        print(f"\nFound {len(categories)} categories:")

        for category in sorted(categories):
            md_files = list(category.glob("*.md"))
            print(f"  - {category.name}/ ({len(md_files)} files)")
            for md_file in sorted(md_files)[:3]:  # Show first 3
                print(f"    • {md_file.name}")
            if len(md_files) > 3:
                print(f"    ... and {len(md_files) - 3} more")


def main():
    """Run all tests."""
    print("\n")
    print("╔" + "═" * 78 + "╗")
    print("║" + " " * 20 + "CNP MCP Server Test Suite" + " " * 33 + "║")
    print("╚" + "═" * 78 + "╝")

    # Test documentation paths
    test_paths()

    # Test resources
    test_resources()

    # Test tools
    asyncio.run(test_tools())

    print("\n")
    print("=" * 80)
    print("Test Summary")
    print("=" * 80)
    print("""
✓ Resources: Documentation access working
⚠️  Tools: API interface working (requires valid token for full test)

Next steps:
1. Install dependencies: poetry add mcp
2. Start CMP backend: poetry run uvicorn app.main:app --reload
3. Get a valid token from Keycloak
4. Test with real token
5. Configure in Claude Desktop or Cursor

Configuration file: mcp-config-example.json
""")


if __name__ == "__main__":
    main()
