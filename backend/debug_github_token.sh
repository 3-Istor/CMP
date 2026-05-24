#!/bin/bash
# GitHub App Token Generator (Bash version)
# Usage: ./debug_github_token.sh [installation_id]

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "============================================================"
echo "GitHub App Token Generator (Debug Tool)"
echo "============================================================"
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo -e "${RED}❌ ERROR: .env file not found${NC}"
    echo ""
    echo "Please create backend/.env with your GitHub App credentials"
    exit 1
fi

# Load .env
source .env

# Check if private key is set
if [ -z "$GITHUB_APP_PRIVATE_KEY" ]; then
    echo -e "${RED}❌ ERROR: GITHUB_APP_PRIVATE_KEY not set in .env${NC}"
    exit 1
fi

# Get installation ID
if [ -n "$1" ]; then
    INSTALLATION_ID="$1"
elif [ -n "$GITHUB_INSTALLATION_ID" ]; then
    INSTALLATION_ID="$GITHUB_INSTALLATION_ID"
    echo -e "${BLUE}ℹ️  Using installation_id from .env${NC}"
else
    echo -e "${RED}❌ ERROR: No installation_id provided${NC}"
    echo ""
    echo "Usage:"
    echo "  ./debug_github_token.sh <installation_id>"
    echo ""
    echo "Or set GITHUB_INSTALLATION_ID in .env"
    echo ""
    echo "To find your installation_id:"
    echo "  1. Go to https://github.com/settings/installations"
    echo "  2. Click on 'Configure' for CNP-Portal"
    echo "  3. The installation_id is in the URL"
    exit 1
fi

echo -e "${BLUE}📋 Installation ID: $INSTALLATION_ID${NC}"
echo -e "${BLUE}📋 App ID: ${GITHUB_APP_ID:-3836905}${NC}"
echo ""

# Use Python script
echo -e "${GREEN}🔐 Generating token via Python service...${NC}"
echo ""

poetry run python debug_github_token.py "$INSTALLATION_ID"
