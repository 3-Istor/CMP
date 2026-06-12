#!/usr/bin/env python3
"""
Debug script to generate a GitHub App installation token.
Usage: python debug_github_token.py [installation_id]
"""

import asyncio
import os
import sys
from pathlib import Path

# Add the backend directory to the path
sys.path.insert(0, str(Path(__file__).parent))

from app.core.config import settings
from app.services import github_service


async def main():
    """Generate and display a GitHub App installation token."""

    print("=" * 60)
    print("GitHub App Token Generator (Debug Tool)")
    print("=" * 60)
    print()

    # Check if private key is configured
    if not settings.GITHUB_APP_PRIVATE_KEY:
        print("❌ ERROR: GITHUB_APP_PRIVATE_KEY not configured in .env")
        print()
        print("Please add your GitHub App private key to backend/.env:")
        print('GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\\n...\\n-----END RSA PRIVATE KEY-----"')
        return 1

    # Get installation ID from command line or use default
    if len(sys.argv) > 1:
        installation_id = sys.argv[1]
    else:
        print("ℹ️  No installation_id provided, using default from .env")
        installation_id = os.getenv("GITHUB_INSTALLATION_ID")

        if not installation_id:
            print()
            print("❌ ERROR: No installation_id provided")
            print()
            print("Usage:")
            print("  python debug_github_token.py <installation_id>")
            print()
            print("Or set GITHUB_INSTALLATION_ID in .env")
            print()
            print("To find your installation_id:")
            print("  1. Go to https://github.com/settings/installations")
            print("  2. Click on 'Configure' for CNP-Portal")
            print("  3. The installation_id is in the URL:")
            print("     https://github.com/settings/installations/12345678")
            print("                                              ^^^^^^^^")
            return 1

    print(f"📋 Installation ID: {installation_id}")
    print(f"📋 App ID: {github_service.GITHUB_APP_ID}")
    print()

    # Generate JWT
    try:
        print("🔐 Generating JWT...")
        jwt_token = github_service.generate_jwt()
        print(f"✅ JWT generated (expires in 10 minutes)")
        print(f"   JWT: {jwt_token[:50]}...")
        print()
    except Exception as e:
        print(f"❌ Failed to generate JWT: {e}")
        print()
        print("Common issues:")
        print("  - Invalid private key format")
        print("  - Private key doesn't match the App ID")
        return 1

    # Get installation token (ASYNC)
    try:
        print(f"🔑 Requesting installation token for installation {installation_id}...")
        token = await github_service.get_installation_token(installation_id)
        print("✅ Installation token retrieved!")
        print()
        print("=" * 60)
        print("TOKEN (valid for 1 hour):")
        print("=" * 60)
        print(token)
        print("=" * 60)
        print()
        print("💡 Usage examples:")
        print()
        print("  # List repositories")
        print(f'  curl -H "Authorization: Bearer {token[:20]}..." \\')
        print('       https://api.github.com/installation/repositories')
        print()
        print("  # Create a repository")
        print(f'  curl -X POST -H "Authorization: Bearer {token[:20]}..." \\')
        print('       -H "Content-Type: application/json" \\')
        print('       -d \'{"name":"test-repo","private":true}\' \\')
        print('       https://api.github.com/user/repos')
        print()
        print("  # Export as environment variable")
        print(f'  export GITHUB_TOKEN="{token}"')
        print()
        print("  # Test with curl")
        print(f'  curl -H "Authorization: Bearer {token}" \\')
        print('       https://api.github.com/installation/repositories | jq')
        print()

        return 0

    except Exception as e:
        print(f"❌ Failed to get installation token: {e}")
        print()
        print("Common issues:")
        print("  - Invalid installation_id")
        print("  - GitHub App not installed on the account")
        print("  - Incorrect private key")
        print("  - Network connectivity issues")
        print()
        print("Debug steps:")
        print("  1. Verify installation at: https://github.com/settings/installations")
        print("  2. Check that CNP-Portal is installed")
        print("  3. Verify the installation_id in the URL")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
