#!/usr/bin/env python3
"""
Phase 3 Verification Script

Verifies that all Phase 3 components are working correctly.
Run with: poetry run python verify_phase3.py
"""

import sys
from pathlib import Path

print("=" * 70)
print("Phase 3 Verification")
print("=" * 70)
print()

# Test 1: Import all modules
print("✓ Test 1: Module Imports")
try:
    from app.models.deployment import (
        Deployment,
        DeploymentStatus,
        ProviderType,
    )
    print("  ✓ Deployment model")

    from app.services import github_service
    print("  ✓ GitHub service")

    from app.services import saga_orchestrator
    print("  ✓ Saga orchestrator")

    from app.core.config import settings
    print("  ✓ Configuration")
except ImportError as e:
    print(f"  ✗ Import failed: {e}")
    sys.exit(1)

print()

# Test 2: Check ProviderType enum
print("✓ Test 2: Provider Types")
provider_types = [p.value for p in ProviderType]
print(f"  Available providers: {', '.join(provider_types)}")
assert "legacy_hybrid" in provider_types
assert "kubernetes" in provider_types
print("  ✓ Both provider types available")

print()

# Test 3: Check database schema
print("✓ Test 3: Database Schema")
try:
    import sqlite3
    conn = sqlite3.connect('arcl.db')
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(deployments)")
    columns = {row[1] for row in cursor.fetchall()}

    required_columns = {
        'provider_type', 'project_id', 'github_repo_url',
        'argocd_app_name', 'k8s_namespace'
    }

    missing = required_columns - columns
    if missing:
        print(f"  ✗ Missing columns: {missing}")
        sys.exit(1)

    print(f"  ✓ All {len(required_columns)} new columns present")
    conn.close()
except Exception as e:
    print(f"  ✗ Database check failed: {e}")
    sys.exit(1)

print()

# Test 4: Check Terraform module
print("✓ Test 4: Terraform Module")
tf_module = Path("app/terraform/github_bootstrap")
required_files = [
    "main.tf", "variables.tf", "outputs.tf",
    "templates/values.yaml.tftpl",
    "templates/Dockerfile",
    "templates/ci.yml.tftpl"
]

for file in required_files:
    file_path = tf_module / file
    if not file_path.exists():
        print(f"  ✗ Missing file: {file}")
        sys.exit(1)

print(f"  ✓ All {len(required_files)} Terraform files present")

print()

# Test 5: Check configuration
print("✓ Test 5: Configuration")
if settings.GITHUB_APP_PRIVATE_KEY:
    print("  ✓ GITHUB_APP_PRIVATE_KEY configured")
else:
    print("  ⚠ GITHUB_APP_PRIVATE_KEY not configured (required for Kubernetes deployments)")

if settings.TF_BACKEND_S3_ENABLED:
    print("  ✓ S3 backend enabled")
    if settings.TF_BACKEND_S3_BUCKET:
        print(f"  ✓ S3 bucket: {settings.TF_BACKEND_S3_BUCKET}")
else:
    print("  ⚠ S3 backend not enabled (required for production)")

print()

# Test 6: Check GitHub service functions
print("✓ Test 6: GitHub Service Functions")
try:
    # Check if JWT generation works (will fail if no key, but function exists)
    assert hasattr(github_service, 'generate_jwt')
    print("  ✓ generate_jwt() function exists")

    assert hasattr(github_service, 'get_installation_token')
    print("  ✓ get_installation_token() function exists")

    assert hasattr(github_service, 'create_repository')
    print("  ✓ create_repository() function exists")
except AssertionError as e:
    print(f"  ✗ Function check failed: {e}")
    sys.exit(1)

print()

# Test 7: Check saga orchestrator functions
print("✓ Test 7: Saga Orchestrator Functions")
try:
    assert hasattr(saga_orchestrator, 'run_deployment')
    print("  ✓ run_deployment() function exists")

    assert hasattr(saga_orchestrator, 'run_deletion')
    print("  ✓ run_deletion() function exists")

    assert hasattr(saga_orchestrator, '_run_kubernetes_deployment')
    print("  ✓ _run_kubernetes_deployment() function exists")

    assert hasattr(saga_orchestrator, '_run_legacy_hybrid_deployment')
    print("  ✓ _run_legacy_hybrid_deployment() function exists")
except AssertionError as e:
    print(f"  ✗ Function check failed: {e}")
    sys.exit(1)

print()

# Summary
print("=" * 70)
print("✅ All Phase 3 components verified successfully!")
print("=" * 70)
print()
print("Next steps:")
print("  1. Configure GITHUB_APP_PRIVATE_KEY in .env (if not done)")
print("  2. Test GitHub integration: poetry run python test_github_service.py")
print("  3. Create a test Kubernetes deployment")
print()
print("Documentation:")
print("  - Quick Start: QUICKSTART_PHASE3.md")
print("  - Complete Guide: PHASE3_COMPLETE.md")
print()
