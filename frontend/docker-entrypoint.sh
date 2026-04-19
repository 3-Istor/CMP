#!/bin/sh
set -e

echo "🚀 Starting ARCL CMP Frontend..."
echo "📝 Configuring runtime environment..."

# Create runtime config with actual environment variable
# Try to write to public directory, fallback if permission denied
if cat > /app/public/config.js << EOF 2>/dev/null
// Runtime configuration - Generated at container startup
window.__RUNTIME_CONFIG__ = {
  apiUrl: '${NEXT_PUBLIC_API_URL:-http://localhost:8000/api}'
};
EOF
then
    echo "✅ API URL configured: ${NEXT_PUBLIC_API_URL:-http://localhost:8000/api}"
else
    echo "⚠️  Cannot write to /app/public/config.js (permission denied)"
    echo "⚠️  Using environment variable fallback"
fi
echo "🌐 Starting Next.js server..."

# Start Next.js
exec node server.js
