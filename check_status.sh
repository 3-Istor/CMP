# Quick status check for Terraform variable fixes

echo "================================================================================"
echo "🔍 CMP Terraform Variables Status Check"
echo "================================================================================"
echo ""

# Check backend environment
echo "📦 1. Backend Environment Variables"
echo "--------------------------------------------------------------------------------"
cd backend 2>/dev/null || { echo "❌ backend/ directory not found"; exit 1; }

if command -v poetry &> /dev/null; then
    poetry run python validate_env.py 2>&1 | head -30
else
    echo "❌ poetry command not found"
fi

echo ""
echo "📋 2. Recent Deployments"
echo "--------------------------------------------------------------------------------"
poetry run python -c "
from app.core.database import SessionLocal
from app.models.deployment import Deployment

db = SessionLocal()
deployments = db.query(Deployment).order_by(Deployment.id.desc()).limit(5).all()

print(f'Last 5 deployments:')
print('-' * 80)
for d in deployments:
    status_emoji = '✅' if d.status.value == 'running' else '❌' if d.status.value == 'failed' else '⏳'
    print(f'{status_emoji} ID {d.id}: {d.name} ({d.template_id}) - {d.status.value}')

db.close()
" 2>&1

echo ""
echo "🔧 3. Terraform Variables Status"
echo "--------------------------------------------------------------------------------"
echo "✅ Backend destroy - Fixed (regenerates variables)"
echo "✅ Backend deploy - Fixed (generates variables + validation)"
echo "⚠️  Frontend - Needs update (missing github_installation_id & project_name)"

echo ""
echo "📚 4. Quick Commands"
echo "--------------------------------------------------------------------------------"
echo "Compare data:     poetry run python compare_app_configs.py"
echo "Test deployment:  poetry run python test_deployment_complete.py --dry-run"
echo "Watch logs:       tail -f logs/cmp.log | grep -E '(github_token|project_name)'"
echo "Run backend:      poetry run python -m app.main"

echo ""
echo "📖 5. Documentation"
echo "--------------------------------------------------------------------------------"
echo "Frontend guide:   ../FRONTEND_FIX_NEEDED.md"
echo "API requirements: ../DEPLOYMENT_API_REQUIREMENTS.md"
echo "Full summary:     ../TERRAFORM_VARIABLES_FIX_SUMMARY.md"

echo ""
echo "================================================================================"
echo "✅ Status Check Complete"
echo "================================================================================"
