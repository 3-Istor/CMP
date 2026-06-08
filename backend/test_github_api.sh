#!/bin/bash
# Test GitHub API with generated token
# Usage: ./test_github_api.sh [installation_id]

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

INSTALLATION_ID="${1:-135177507}"

echo -e "${BLUE}🔑 Generating GitHub token...${NC}"
echo ""

# Generate token and extract it
TOKEN=$(poetry run python debug_github_token.py "$INSTALLATION_ID" 2>/dev/null | grep "^ghs_" | tail -1)

if [ -z "$TOKEN" ]; then
    echo "❌ Failed to generate token"
    exit 1
fi

echo -e "${GREEN}✅ Token generated: ${TOKEN:0:20}...${NC}"
echo ""

# Test 1: List repositories
echo -e "${BLUE}📦 Test 1: Listing accessible repositories...${NC}"
echo ""
curl -s -H "Authorization: Bearer $TOKEN" \
     https://api.github.com/installation/repositories | \
     jq -r '.repositories[] | "  - \(.name) (\(.private | if . then "private" else "public" end))"'
echo ""

# Test 2: Get installation info
echo -e "${BLUE}🔍 Test 2: Installation information...${NC}"
echo ""
curl -s -H "Authorization: Bearer $TOKEN" \
     https://api.github.com/app/installations/$INSTALLATION_ID | \
     jq '{
       account: .account.login,
       type: .account.type,
       permissions: .permissions,
       repository_selection: .repository_selection
     }'
echo ""

# Test 3: Check rate limits
echo -e "${BLUE}⏱️  Test 3: API Rate limits...${NC}"
echo ""
curl -s -H "Authorization: Bearer $TOKEN" \
     https://api.github.com/rate_limit | \
     jq '{
       core: {
         limit: .resources.core.limit,
         remaining: .resources.core.remaining,
         reset: (.resources.core.reset | strftime("%Y-%m-%d %H:%M:%S"))
       }
     }'
echo ""

echo -e "${GREEN}✅ All tests completed!${NC}"
echo ""
echo -e "${YELLOW}💡 Token is valid for 1 hour${NC}"
echo -e "${YELLOW}💡 Export it: export GITHUB_TOKEN=\"$TOKEN\"${NC}"
