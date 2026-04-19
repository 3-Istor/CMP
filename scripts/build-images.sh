#!/bin/bash
set -e

# ARCL CMP Docker Image Build Script

REGISTRY="${REGISTRY:-ghcr.io/3-istor}"
VERSION="${VERSION:-latest}"
BACKEND_IMAGE="$REGISTRY/arcl-cmp-backend"
FRONTEND_IMAGE="$REGISTRY/arcl-cmp-frontend"

echo "🐳 ARCL CMP Docker Image Build Script"
echo "======================================"
echo "Registry: $REGISTRY"
echo "Version: $VERSION"
echo ""

# Check if Docker is available
command -v docker >/dev/null 2>&1 || { echo "❌ Docker is required but not installed."; exit 1; }

# Build backend
echo "🔨 Building backend image..."
docker build -t "$BACKEND_IMAGE:$VERSION" ./backend
echo "✅ Backend image built: $BACKEND_IMAGE:$VERSION"

# Build frontend
echo "🔨 Building frontend image..."
docker build -t "$FRONTEND_IMAGE:$VERSION" ./frontend
echo "✅ Frontend image built: $FRONTEND_IMAGE:$VERSION"

echo ""
echo "✅ All images built successfully!"
echo ""
echo "To push images:"
echo "  docker push $BACKEND_IMAGE:$VERSION"
echo "  docker push $FRONTEND_IMAGE:$VERSION"
echo ""
echo "To test locally with docker-compose:"
echo "  docker-compose up -d"
