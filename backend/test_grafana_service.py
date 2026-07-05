#!/usr/bin/env python3
"""
Interactive test script for Grafana Service integration.

This script tests the Grafana service functions for managing user memberships
in Grafana Organizations.

Usage:
    poetry run python test_grafana_service.py
"""

import asyncio
import logging
import sys

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


async def main():
    """Run interactive Grafana service tests."""
    print("\n" + "=" * 80)
    print("GRAFANA SERVICE TEST SUITE")
    print("=" * 80)

    # Import after logging is configured
    from app.core.config import settings
    from app.services.grafana_service import (
        _grafana_org_name,
        _map_role_to_grafana,
        _title_case_project_name,
        add_user_to_project_org,
        remove_user_from_project_org,
    )

    # ── Test 1: Configuration ──────────────────────────────────────────────
    print("\n[Test 1] Checking configuration...")
    if not settings.GRAFANA_ADMIN_PASSWORD:
        print("❌ GRAFANA_ADMIN_PASSWORD not set in .env")
        print("   Please add: GRAFANA_ADMIN_PASSWORD=your_password")
        sys.exit(1)
    print("✅ GRAFANA_ADMIN_PASSWORD configured")

    # ── Test 2: Helper functions ───────────────────────────────────────────
    print("\n[Test 2] Testing helper functions...")
    test_cases = [
        ("alpha", "Project Alpha"),
        ("my-team", "Project My Team"),
        ("sandbox", "Project Sandbox"),
        ("dev-ops-team", "Project Dev Ops Team"),
    ]

    for project_name, expected_org_name in test_cases:
        actual = _grafana_org_name(project_name)
        if actual == expected_org_name:
            print(f"  ✅ '{project_name}' → '{actual}'")
        else:
            print(
                f"  ❌ '{project_name}' → '{actual}' (expected: '{expected_org_name}')"
            )

    # Test role mapping
    role_test_cases = [
        ("admin", "Admin"),
        ("owner", "Admin"),
        ("member", "Editor"),
        ("unknown", "Viewer"),  # fallback
    ]

    print("\n  Role mappings:")
    for cmp_role, expected_grafana_role in role_test_cases:
        actual = _map_role_to_grafana(cmp_role)
        if actual == expected_grafana_role:
            print(f"    ✅ '{cmp_role}' → '{actual}'")
        else:
            print(
                f"    ❌ '{cmp_role}' → '{actual}' (expected: '{expected_grafana_role}')"
            )

    # ── Test 3: Interactive API Tests ──────────────────────────────────────
    print("\n[Test 3] Interactive API tests")
    print("\nThis will test the Grafana Admin API with real requests.")
    print("You can provide a test project and username to verify operations.")

    response = input("\nRun interactive tests? (y/N): ").strip().lower()
    if response != "y":
        print("\n✅ Unit tests passed. Skipping interactive tests.")
        return

    project_name = input(
        "\nEnter test project name (e.g., 'sandbox'): "
    ).strip()
    if not project_name:
        print("❌ Project name required")
        return

    username = input("Enter test username (e.g., 'brian.perret'): ").strip()
    if not username:
        print("❌ Username required")
        return

    role = (
        input("Enter role (admin/member) [default: member]: ").strip()
        or "member"
    )

    # ── Test 3a: Add user to project org ───────────────────────────────────
    print(
        f"\n[Test 3a] Adding user '{username}' to project '{project_name}' with role '{role}'..."
    )
    success = await add_user_to_project_org(project_name, username, role)

    if success:
        print(f"✅ Successfully added user to Grafana org")
    else:
        print("⚠️  Failed to add user (check logs above for details)")
        print("   This is expected if:")
        print(
            "   - The Grafana org doesn't exist yet (Terraform hasn't created it)"
        )
        print(
            "   - The user hasn't logged in to Grafana yet (no Grafana account)"
        )

    # ── Test 3b: Update user role ──────────────────────────────────────────
    if success:
        print(f"\n[Test 3b] Updating user role to 'admin'...")
        success = await add_user_to_project_org(
            project_name, username, "admin"
        )
        if success:
            print("✅ Successfully updated user role")
        else:
            print("⚠️  Failed to update user role")

    # ── Test 3c: Remove user from project org ─────────────────────────────
    remove_test = input("\nRemove user from org? (y/N): ").strip().lower()
    if remove_test == "y":
        print(
            f"\n[Test 3c] Removing user '{username}' from project '{project_name}'..."
        )
        success = await remove_user_from_project_org(project_name, username)
        if success:
            print("✅ Successfully removed user from Grafana org")
        else:
            print("⚠️  Failed to remove user")

    print("\n" + "=" * 80)
    print("TEST SUITE COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
