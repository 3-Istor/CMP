#!/bin/bash

# CMP Setup Script
# This script automates the initial setup of the Cloud Management Platform

set -e

echo "=========================================="
echo "CMP - Automated Setup"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check prerequisites
echo "Checking prerequisites..."

check_command() {
    if ! command -v $1 &> /dev/null; then
        echo -e "${RED}✗ $1 is not installed${NC}"
        echo "  Please install $1 and try again"
        exit 1
    else
        echo -e "${GREEN}✓ $1 is installed${NC}"
    fi
}

check_command python3
check_command poetry
check_command terraform
check_command git
check_command node
check_command npm

echo ""
echo "=========================================="
echo "Backend Setup"
echo "=========================================="
echo ""

cd backend

# Install Python dependencies
echo "Installing Python dependencies..."
poetry install

# Setup environment file
if [ ! -f .env ]; then
    echo "Creating .env file..."
    cp .env.example .env
    echo -e "${YELLOW}⚠ Please edit backend/.env with your credentials${NC}"
else
    echo -e "${GREEN}✓ .env file already exists${NC}"
fi

# Run database migrations
echo "Running database migrations..."
poetry run alembic upgrade head

echo -e "${GREEN}✓ Backend setup complete${NC}"

cd ..

echo ""
echo "=========================================="
echo "Frontend Setup"
echo "=========================================="
echo ""

cd frontend

# Install Node dependencies
echo "Installing Node dependencies..."
npm install

# Setup environment file
if [ ! -f .env.local ]; then
    echo "Creating .env.local file..."
    cp .env.local.example .env.local
    echo -e "${GREEN}✓ .env.local created${NC}"
else
    echo -e "${GREEN}✓ .env.local file already exists${NC}"
fi

echo -e "${GREEN}✓ Frontend setup complete${NC}"

cd ..

echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo ""
echo "1. Edit backend/.env with your OpenStack credentials"
echo ""
echo "2. Start the backend:"
echo "   cd backend"
echo "   poetry run uvicorn app.main:app --reload --port 8000"
echo ""
echo "3. In a new terminal, start the frontend:"
echo "   cd frontend"
echo "   npm run dev"
echo ""
echo "4. Open http://localhost:3000 in your browser"
echo ""
echo "For more information, see SETUP_INSTRUCTIONS.md"
echo ""
