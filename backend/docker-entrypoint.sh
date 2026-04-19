#!/bin/bash
set -e

echo "🚀 Starting ARCL CMP Backend..."

# Extract database path from DATABASE_URL environment variable
# Handle both sqlite:///./file.db and sqlite:////absolute/path/file.db formats
DB_PATH=$(echo "${DATABASE_URL:-sqlite:///./arcl.db}" | sed 's|sqlite:///\./|/app/|' | sed 's|sqlite:///||')

echo "📊 Database path: $DB_PATH"

# Ensure database directory exists
DB_DIR=$(dirname "$DB_PATH")
if [ ! -d "$DB_DIR" ]; then
    echo "📁 Creating database directory: $DB_DIR"
    mkdir -p "$DB_DIR" || echo "⚠️  Could not create directory (may already exist)"
fi

# Fix permissions if running as root in K8s
if [ "$(id -u)" = "0" ]; then
    echo "� Runninsg as root, ensuring directory permissions..."
    chmod -R 777 "$DB_DIR" 2>/dev/null || echo "⚠️  Could not change directory permissions"
    if [ -f "$DB_PATH" ]; then
        chmod 666 "$DB_PATH" 2>/dev/null || echo "⚠️  Could not change file permissions"
    fi
fi

# List files to debug
echo "📁 Checking database directory contents..."
ls -la "$DB_DIR"/*.db 2>/dev/null || echo "No .db files found in $DB_DIR"

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
            echo "⚠️  Attempting to delete and recreate database..."

            if rm -f "$DB_PATH" 2>/dev/null; then
                echo "✅ Old database deleted"
            else
                echo "❌ Cannot delete database file - permission denied"
                echo "💡 Please delete the persistent volume and restart:"
                echo "   kubectl delete pvc <your-pvc-name>"
                exit 1
            fi

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
