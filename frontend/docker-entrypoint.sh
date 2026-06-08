#!/bin/sh
set -e

echo "🚀 Starting CMP Frontend..."
echo "📝 Configuring runtime environment..."

# Export the API URL as an environment variable for Next.js runtime
export NEXT_PUBLIC_API_URL="${NEXT_PUBLIC_API_URL:-http://localhost:8000/api}"

echo "✅ API URL configured: $NEXT_PUBLIC_API_URL"

# Try to write config.js for browser access (best effort)
cat > /app/public/config.js << EOF 2>/dev/null || echo "⚠️  Could not write config.js (using env fallback)"
// Runtime configuration - Generated at container startup
window.__RUNTIME_CONFIG__ = {
  apiUrl: '${NEXT_PUBLIC_API_URL}'
};
EOF

echo "🌐 Starting Next.js server..."

# Start Next.js
exec node server.js
