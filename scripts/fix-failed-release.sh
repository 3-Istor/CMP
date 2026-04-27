#!/bin/bash
set -e

echo "🔧 Fix Failed Release Script"
echo "============================="
echo ""

# Check if version argument is provided
if [ -z "$1" ]; then
    echo "Usage: ./scripts/fix-failed-release.sh <version>"
    echo "Example: ./scripts/fix-failed-release.sh 0.0.5"
    echo ""
    echo "This will:"
    echo "  1. Delete local and remote tags"
    echo "  2. Recreate and push tags to trigger new build"
    exit 1
fi

VERSION=$1

echo "Fixing release for version: $VERSION"
echo ""

# Confirm
read -p "This will delete and recreate v$VERSION and helm-v$VERSION. Continue? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted"
    exit 1
fi

# Delete local tags
echo "🗑️  Deleting local tags..."
git tag -d "v$VERSION" 2>/dev/null || echo "  Local tag v$VERSION not found"
git tag -d "helm-v$VERSION" 2>/dev/null || echo "  Local tag helm-v$VERSION not found"

# Delete remote tags
echo "🗑️  Deleting remote tags..."
git push origin ":refs/tags/v$VERSION" 2>/dev/null || echo "  Remote tag v$VERSION not found"
git push origin ":refs/tags/helm-v$VERSION" 2>/dev/null || echo "  Remote tag helm-v$VERSION not found"

echo "✅ Old tags deleted"
echo ""

# Wait a moment
echo "⏳ Waiting 5 seconds..."
sleep 5

# Recreate and push
echo "📦 Creating new tags..."
git tag -a "v$VERSION" -m "Release v$VERSION"
git tag -a "helm-v$VERSION" -m "Helm chart release v$VERSION"

echo "⬆️  Pushing v$VERSION to GitHub..."
git push origin "v$VERSION"
echo "✅ Pushed v$VERSION"

echo "⏳ Waiting 10 seconds for Docker build to start..."
sleep 10

echo "⬆️  Pushing helm-v$VERSION to GitHub..."
git push origin "helm-v$VERSION"
echo "✅ Pushed helm-v$VERSION"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Tags recreated and pushed!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Monitor workflows at:"
echo "  https://github.com/$(git config --get remote.origin.url | sed 's/.*github.com[:/]\(.*\)\.git/\1/')/actions"
echo ""
echo "Packages will appear at:"
echo "  https://github.com/orgs/3-Istor/packages?repo_name=CMP"
echo ""
