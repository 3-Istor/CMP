#!/bin/bash
set -e

echo "🚀 Starting ARCL CMP Backend..."

# Ensure database directory exists with proper permissions
mkdir -p /app/db || true
chmod 755 /app/db 2>/dev/null || echo "⚠️  Could not change db directory permissions (may be mounted volume)"

# Check if database exists
if [ -f "/app/db/arcl.db" ]; then
    echo "📊 Database file exists, checking if it's valid..."

    # Check if database is valid by trying to open it
    if sqlite3 /app/db/arcl.db "SELECT 1;" 2>/dev/null; then
        echo "✅ Database is valid, running migrations..."
        if alembic upgrade head 2>/dev/null; then
            echo "✅ Migrations completed successfully"
        else
            echo "⚠️  Migration failed, attempting to stamp current state..."
            if alembic stamp head 2>/dev/null; then
                echo "✅ Database stamped with current migration"
            else
                echo "❌ Failed to stamp database, recreating..."
                rm -f /app/db/arcl.db
                alembic upgrade head
                echo "✅ Database recreated and initialized"
            fi
        fi
    else
        echo "⚠️  Database file is corrupted, recreating..."
        rm -f /app/db/arcl.db
        alembic upgrade head
        echo "✅ Database recreated and initialized"
    fi
else
    echo "📊 Creating new database..."
    alembic upgrade head
    echo "✅ Database initialized"
fi

echo "🌐 Starting FastAPI server..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
