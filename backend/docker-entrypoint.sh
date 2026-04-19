#!/bin/bash
set -e

echo "🚀 Starting ARCL CMP Backend..."

# Check if database exists and has tables
if [ -f "/app/arcl.db" ]; then
    echo "📊 Database exists, checking migration state..."
    # Try to stamp the current revision if migrations fail
    alembic upgrade head 2>/dev/null || {
        echo "⚠️  Migration failed, attempting to stamp current state..."
        alembic stamp head
        echo "✅ Database stamped with current migration"
    }
else
    echo "📊 Creating new database..."
    alembic upgrade head
    echo "✅ Database initialized"
fi

echo "🌐 Starting FastAPI server..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
