set -e

echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║                                                                  ║"
echo "║   🧪 Testing Runtime Configuration                              ║"
echo "║                                                                  ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Step 1: Build
echo "📦 Step 1: Building frontend image..."
cd frontend
docker build -t arcl-frontend-test:local .
cd ..
echo -e "${GREEN}✅ Build complete${NC}"
echo ""

# Step 2: Run with dummy URL
echo "🚀 Step 2: Running container with dummy API URL..."

# Clean up any existing container
if docker ps -a --format '{{.Names}}' | grep -q '^arcl-frontend-test$'; then
  echo "🧹 Removing existing container..."
  docker rm -f arcl-frontend-test > /dev/null 2>&1
fi

docker run -d \
  --name arcl-frontend-test \
  -p 3001:3000 \
  -e NEXT_PUBLIC_API_URL="https://dummy-api.example.com/api" \
  arcl-frontend-test:local

echo "⏳ Waiting for container to start..."
sleep 5
echo ""

# Step 3: Verify
echo "🔍 Step 3: Verifying runtime configuration..."
echo ""

echo "📋 Container logs:"
docker logs arcl-frontend-test
echo ""

echo "📄 Generated config.js:"
docker exec arcl-frontend-test cat /app/public/config.js
echo ""

echo "🌐 Testing config.js endpoint:"
curl -s http://localhost:3001/config.js
echo ""
echo ""

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}✅ Test Complete!${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "🌐 Open your browser to: http://localhost:3001"
echo ""
echo "In browser DevTools console, type:"
echo "  window.__RUNTIME_CONFIG__"
echo ""
echo "Expected output:"
echo "  {apiUrl: \"https://dummy-api.example.com/api\"}"
echo ""
echo "In Network tab, API calls should go to:"
echo "  https://dummy-api.example.com/api/catalog/"
echo "  https://dummy-api.example.com/api/deployments/"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "To test with a different URL:"
echo "  docker stop arcl-frontend-test && docker rm arcl-frontend-test"
echo "  docker run -d --name arcl-frontend-test -p 3001:3000 \\"
echo "    -e NEXT_PUBLIC_API_URL=\"https://another-url.com/api\" \\"
echo "    arcl-frontend-test:local"
echo ""
echo "To cleanup:"
echo "  docker stop arcl-frontend-test"
echo "  docker rm arcl-frontend-test"
echo "  docker rmi arcl-frontend-test:local"
echo ""
