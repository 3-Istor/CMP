#!/bin/sh
set -e

echo "🚀 Starting ARCL CMP Frontend..."
echo "📝 Configuring runtime environment..."

# Create runtime config with actual environment variable
cat > /app/public/config.js << EOF
// Runtime configuration - Generated at container startup
window.__RUNTIME_CONFIG__ = {
  apiUrl: '${NEXT_PUBLIC_API_URL:-http://localhost:8000/api}'
};
EOF

echo "✅ API URL configured: ${NEXT_PUBLIC_API_URL:-http://localhost:8000/api}"
echo "🌐 Starting Next.js server..."

# Start Next.js
exec node server.js
