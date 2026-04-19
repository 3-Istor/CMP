#!/bin/bash
set -e

echo "🚀 Starting ARCL CMP Backend..."

# Database path from DATABASE_URL environment variable
DB_PATH="/app/arcl.db"

# List files to debug
echo "📁 Checking /app directory contents..."
ls -la /app/*.db 2>/dev/null || echo "No .db files found in /app"

# Check if database exists
if [ -f "$DB_PATH" ]; then
    echo "📊 Database file exists at $DB_PATH, checking if it's valid..."

    # Try to check for alembic_version table
    if sqlite3 "$DB_PATH" "SELECT version_num FROM alembic_version LIMIT 1;" 2>/dev/null; then
        echo "✅ Database has migration tracking, running migrations..."
        alembic upgrade head
        echo "✅ Migrations completed successfully"
    else
        # Check if database has any tables
        TABLE_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sqlite_master WHERE type='table';" 2>/dev/null || echo "0")

        if [ "$TABLE_COUNT" -gt 0 ]; then
            echo "⚠️  Database has $TABLE_COUNT tables but no migration tracking"
            echo "⚠️  Deleting old database and recreating..."
            rm -f "$DB_PATH"
            alembic upgrade head
            echo "✅ Database recreated and initialized"
        else
            echo "⚠️  Database file exists but is empty, initializing..."
            alembic upgrade head
            echo "✅ Database initialized"
        fi
    fi
else
    echo "📊 No database file found, creating new database..."
    alembic upgrade head
    echo "✅ Database initialized"
fi

echo "🌐 Starting FastAPI server..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
