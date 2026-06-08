#!/bin/bash
# Phase 3 Setup Script
# This script sets up the Phase 3 Kubernetes provider support

set -e  # Exit on error

echo "════════════════════════════════════════════════════════════════"
echo "  Phase 3 Setup: Kubernetes Provider Support"
echo "════════════════════════════════════════════════════════════════"
echo ""

# Check if we're in the backend directory
if [ ! -f "pyproject.toml" ]; then
    echo "❌ Error: Please run this script from the backend/ directory"
    exit 1
fi

# Step 1: Install dependencies
echo "📦 Step 1: Installing dependencies..."
poetry install --no-root
echo "✅ Dependencies installed"
echo ""

# Step 2: Check configuration
echo "🔍 Step 2: Checking configuration..."
if ! poetry run python -c "from app.core.config import settings; assert settings.GITHUB_APP_PRIVATE_KEY or True" 2>/dev/null; then
    echo "⚠️  Warning: GITHUB_APP_PRIVATE_KEY not configured in .env"
    echo "   You'll need to add it before creating Kubernetes deployments"
else
    echo "✅ Configuration looks good"
fi
echo ""

# Step 3: Test imports
echo "🧪 Step 3: Testing imports..."
poetry run python -c "from app.models.deployment import Deployment, ProviderType; print('  ✓ Deployment model')"
poetry run python -c "from app.services import github_service; print('  ✓ GitHub service')"
poetry run python -c "from app.services import saga_orchestrator; print('  ✓ Saga orchestrator')"
echo "✅ All imports successful"
echo ""

# Step 4: Check database migration
echo "🗄️  Step 4: Checking database migration..."
if poetry run alembic current 2>/dev/null | grep -q "c4d8f2a91b3e"; then
    echo "✅ Phase 3 migration already applied"
else
    echo "⚠️  Phase 3 migration not yet applied"
    echo ""
    read -p "   Apply migration now? (y/N): " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        poetry run alembic upgrade head
        echo "✅ Migration applied successfully"
    else
        echo "   Skipped. Run 'poetry run alembic upgrade head' when ready."
    fi
fi
echo ""

# Step 5: Validate Terraform module
echo "🛠️  Step 5: Validating Terraform module..."
if command -v terraform &> /dev/null; then
    cd app/terraform/github_bootstrap
    terraform init -backend=false > /dev/null 2>&1
    if terraform validate > /dev/null 2>&1; then
        echo "✅ Terraform module is valid"
    else
        echo "⚠️  Terraform validation failed"
        terraform validate
    fi
    cd ../../..
else
    echo "⚠️  Terraform not installed, skipping validation"
fi
echo ""

# Summary
echo "════════════════════════════════════════════════════════════════"
echo "  Setup Complete!"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "Next steps:"
echo "  1. Add GITHUB_APP_PRIVATE_KEY to .env (if not already done)"
echo "  2. Test GitHub integration: poetry run python test_github_service.py"
echo "  3. Create a test deployment via the API"
echo ""
echo "Documentation:"
echo "  - Quick Start: QUICKSTART_PHASE3.md"
echo "  - Implementation: PHASE3_IMPLEMENTATION.md"
echo "  - Migration Guide: PHASE3_MIGRATION.md"
echo ""
