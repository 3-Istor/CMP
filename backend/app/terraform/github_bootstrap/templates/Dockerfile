# ══════════════════════════════════════════════════════════════════════════
# Multi-stage Dockerfile Template
# ══════════════════════════════════════════════════════════════════════════
# This is a placeholder Dockerfile. Replace with your actual build steps.

FROM node:20-alpine AS builder

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci --only=production

# Copy application code
COPY . .

# Build the application (if needed)
# RUN npm run build

# ──────────────────────────────────────────────────────────────────────────
# Production Stage
# ──────────────────────────────────────────────────────────────────────────
FROM node:20-alpine

WORKDIR /app

# Copy dependencies and built artifacts from builder
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app .

# Run as non-root user (required by Kyverno policies)
USER node

# Expose application port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD node -e "require('http').get('http://localhost:8080/health', (r) => process.exit(r.statusCode === 200 ? 0 : 1))"

# Start the application
CMD ["node", "index.js"]
