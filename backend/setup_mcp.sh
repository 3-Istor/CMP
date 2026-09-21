#!/bin/bash

# CNP MCP Server Setup Script
# This script automates the setup of the MCP server for Claude Desktop

set -e

echo "╔════════════════════════════════════════════════════════════════════════════╗"
echo "║                    CNP MCP Server Setup Script                             ║"
echo "╚════════════════════════════════════════════════════════════════════════════╝"
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Get absolute path to backend directory
BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "📁 Backend directory: $BACKEND_DIR"
echo ""

# Step 1: Check Poetry
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "1. Checking Poetry installation..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if ! command -v poetry &> /dev/null; then
    echo -e "${RED}✗ Poetry not found${NC}"
    echo "Please install Poetry: https://python-poetry.org/docs/#installation"
    exit 1
fi

echo -e "${GREEN}✓ Poetry found: $(poetry --version)${NC}"
echo ""

# Step 2: Install dependencies
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "2. Installing dependencies..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

cd "$BACKEND_DIR"
poetry install

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Dependencies installed${NC}"
else
    echo -e "${RED}✗ Failed to install dependencies${NC}"
    exit 1
fi
echo ""

# Step 3: Test MCP server
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "3. Testing MCP server..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

poetry run python test_mcp_server.py

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ MCP server test passed${NC}"
else
    echo -e "${RED}✗ MCP server test failed${NC}"
    exit 1
fi
echo ""

# Step 4: Configure Claude Desktop
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "4. Configuring Claude Desktop..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Detect OS
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    CONFIG_DIR="$HOME/.config/Claude"
elif [[ "$OSTYPE" == "darwin"* ]]; then
    CONFIG_DIR="$HOME/Library/Application Support/Claude"
else
    echo -e "${YELLOW}⚠ Unknown OS, skipping Claude Desktop config${NC}"
    CONFIG_DIR=""
fi

if [ -n "$CONFIG_DIR" ]; then
    CONFIG_FILE="$CONFIG_DIR/claude_desktop_config.json"

    # Create config directory if it doesn't exist
    mkdir -p "$CONFIG_DIR"

    # Create config file if it doesn't exist
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "{}" > "$CONFIG_FILE"
        echo "Created empty config file: $CONFIG_FILE"
    fi

    # Generate config content
    cat > "$BACKEND_DIR/claude_desktop_config.json" << EOF
{
  "mcpServers": {
    "cnp-portal": {
      "command": "poetry",
      "args": [
        "run",
        "python",
        "app/mcp_server.py"
      ],
      "cwd": "$BACKEND_DIR",
      "env": {
        "CMP_API_URL": "http://localhost:8000/api",
        "PYTHONUNBUFFERED": "1"
      }
    }
  }
}
EOF

    echo "Generated config file: $BACKEND_DIR/claude_desktop_config.json"
    echo ""
    echo -e "${YELLOW}📝 Next steps for Claude Desktop:${NC}"
    echo "   1. Copy config to Claude Desktop:"
    echo "      cp $BACKEND_DIR/claude_desktop_config.json $CONFIG_FILE"
    echo ""
    echo "   2. Or manually merge with existing config:"
    echo "      cat $BACKEND_DIR/claude_desktop_config.json"
    echo ""
    echo "   3. Restart Claude Desktop"
    echo "   4. Look for 🔌 icon indicating MCP connection"
    echo ""
else
    echo "Skipped Claude Desktop configuration"
    echo ""
fi

# Step 5: Summary
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Setup Complete!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "🎉 MCP server is ready!"
echo ""
echo "📚 Documentation:"
echo "   - Setup guide: $BACKEND_DIR/MCP_SERVER_README.md"
echo "   - Implementation: $BACKEND_DIR/MCP_IMPLEMENTATION_SUMMARY.md"
echo "   - Full guide: $BACKEND_DIR/../.kiro/steering/docs/05-cmp-backend-api/11-cmp-mcp-integration.md"
echo ""
echo "🚀 Next steps:"
echo "   1. Start backend:"
echo "      cd $BACKEND_DIR"
echo "      poetry run uvicorn app.main:app --reload"
echo ""
echo "   2. Test Swagger UI with OAuth2:"
echo "      Open http://localhost:8000/docs"
echo "      Click 'Authorize' and login with Keycloak"
echo ""
echo "   3. Configure Claude Desktop (if not done above)"
echo ""
echo "   4. Ask Claude to read documentation:"
echo '      "Read docs://01-architecture/01-system-overview and explain the CNP architecture"'
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
