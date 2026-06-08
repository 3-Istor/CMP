#!/usr/bin/env python3
"""
Quick test script for GitHub App integration.

Usage:
    poetry run python test_github_service.py

Requires:
    - GITHUB_APP_PRIVATE_KEY set in .env
    - Valid GitHub installation ID
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.core.config import settings
from app.services import github_service


async def test_jwt_generation():
    """Test JWT generation."""
    print("🔐 Testing JWT generation...")

    if not settings.GITHUB_APP_PRIVATE_KEY:
        print("❌ GITHUB_APP_PRIVATE_KEY not configured in .env")
        return False

    try:
        jwt_token = github_service.generate_jwt()
        print(f"✅ JWT generated successfully (length: {len(jwt_token)})")
        print(f"   Token preview: {jwt_token[:50]}...")
        return True
    except Exception as exc:
        print(f"❌ JWT generation failed: {exc}")
        return False


async def test_installation_token(installation_id: str):
    """Test installation token exchange."""
    print(f"\n🔑 Testing installation token exchange for ID: {installation_id}...")

    try:
        token = await github_service.get_installation_token(installation_id)
        print(f"✅ Installation token obtained (length: {len(token)})")
        print(f"   Token preview: {token[:20]}...")
        return token
    except Exception as exc:
        print(f"❌ Installation token exchange failed: {exc}")
        return None


async def test_repository_creation(token: str, repo_name: str):
    """Test repository creation."""
    print(f"\n📦 Testing repository creation: {repo_name}...")

    try:
        repo = await github_service.create_repository(
            installation_token=token,
            repo_name=repo_name,
            org_name=None,  # Creates in user's personal account
            description="Test repository created by CNP"
        )
        print(f"✅ Repository created successfully!")
        print(f"   URL: {repo.get('html_url')}")
        print(f"   Clone URL: {repo.get('clone_url')}")
        return True
    except Exception as exc:
        print(f"❌ Repository creation failed: {exc}")
        return False


async def main():
    """Run all tests."""
    print("=" * 70)
    print("GitHub App Integration Test Suite")
    print("=" * 70)

    # Test 1: JWT Generation
    jwt_ok = await test_jwt_generation()
    if not jwt_ok:
        print("\n⚠️  Cannot proceed without valid JWT. Check your GITHUB_APP_PRIVATE_KEY.")
        return

    # Test 2: Installation Token (requires user input)
    print("\n" + "=" * 70)
    installation_id = input("Enter GitHub installation ID (or press Enter to skip): ").strip()

    if not installation_id:
        print("\n✅ JWT generation test passed. Skipping installation token test.")
        print("\nTo test installation token exchange:")
        print("  1. Install the CNP GitHub App on your account")
        print("  2. Get the installation ID from the callback URL")
        print("  3. Run this script again with the installation ID")
        return

    token = await test_installation_token(installation_id)
    if not token:
        print("\n⚠️  Installation token test failed. Check your installation ID.")
        return

    # Test 3: Repository Creation (optional)
    print("\n" + "=" * 70)
    create_repo = input("Create a test repository? (y/N): ").strip().lower()

    if create_repo == 'y':
        repo_name = input("Repository name (default: cnp-test-repo): ").strip() or "cnp-test-repo"
        await test_repository_creation(token, repo_name)

    print("\n" + "=" * 70)
    print("✅ All tests completed!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
