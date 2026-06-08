#!/bin/bash
set -e

echo "🧪 Testing Docker builds..."
echo ""

# Test backend build
echo "📦 Building backend image..."
docker build -t arcl-cmp-backend:test ./backend
echo "✅ Backend build successful!"
echo ""

# Test frontend build
echo "📦 Building frontend image..."
docker build -t arcl-cmp-frontend:test ./frontend
echo "✅ Frontend build successful!"
echo ""

# Test docker-compose config
echo "📦 Validating docker-compose.yml..."
docker compose config > /dev/null
echo "✅ Docker Compose configuration valid!"
echo ""

echo "✅ All Docker builds passed!"
echo ""
echo "To run locally:"
echo "  docker compose up -d"
