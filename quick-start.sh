#!/bin/bash
# Quick start script for Pulumi EKS deployment

set -e

echo "╔════════════════════════════════════════════════════════════╗"
echo "║     EKS Infrastructure with Pulumi - Quick Start          ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check prerequisites
echo "Checking prerequisites..."

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}✗ Python 3 is required but not installed.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Python 3 found${NC}"

if ! command -v pulumi &> /dev/null; then
    echo -e "${RED}✗ Pulumi is not installed.${NC}"
    echo "  Install: curl -fsSL https://get.pulumi.com | sh"
    exit 1
fi
echo -e "${GREEN}✓ Pulumi found${NC}"

if ! command -v aws &> /dev/null; then
    echo -e "${RED}✗ AWS CLI is required but not installed.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ AWS CLI found${NC}"

# Check AWS credentials
if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${RED}✗ AWS credentials not configured${NC}"
    echo "  Run: aws configure"
    exit 1
fi
echo -e "${GREEN}✓ AWS credentials configured${NC}"

echo ""

PROJECT_DIR="clusters/eks-alpha/infra"
PULUMI_CMD="pulumi -C ${PROJECT_DIR}"

if [ ! -d "${PROJECT_DIR}" ]; then
    echo -e "${RED}✗ Pulumi project directory '${PROJECT_DIR}' not found.${NC}"
    exit 1
fi

# Setup virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
    echo -e "${GREEN}✓ Virtual environment created${NC}"
else
    echo -e "${YELLOW}⚠ Virtual environment already exists${NC}"
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo -e "${GREEN}✓ Dependencies installed${NC}"

echo ""
echo "════════════════════════════════════════════════════════════"
echo ""

# Check if Pulumi is logged in
if ! pulumi whoami &> /dev/null; then
    echo "You need to log in to Pulumi."
    echo ""
    echo "Choose your backend:"
    echo "  1) Local (stores state on this machine)"
    echo "  2) Pulumi Cloud (free for individuals, recommended for teams)"
    echo ""
    read -p "Enter choice [1 or 2]: " backend_choice
    
    if [ "$backend_choice" == "1" ]; then
        pulumi login --local
    else
        pulumi login
    fi
else
    echo -e "${GREEN}✓ Already logged in to Pulumi${NC}"
    pulumi whoami
fi

echo ""

# Check if stack exists
STACK_NAME=${1:-prod}
if ${PULUMI_CMD} stack select "$STACK_NAME" 2>/dev/null; then
    echo -e "${YELLOW}⚠ Stack '$STACK_NAME' already exists${NC}"
    echo "Using existing stack..."
else
    echo "Creating new stack: $STACK_NAME"
    ${PULUMI_CMD} stack init "$STACK_NAME"
    echo -e "${GREEN}✓ Stack created${NC}"
    
    # Copy example config if available
    if [ -f "${PROJECT_DIR}/Pulumi.prod.yaml.example" ] && [ "$STACK_NAME" == "prod" ]; then
        echo "Would you like to use the example configuration? [y/N]"
        read -p "> " use_example
        if [ "$use_example" == "y" ] || [ "$use_example" == "Y" ]; then
            cp "${PROJECT_DIR}/Pulumi.prod.yaml.example" "${PROJECT_DIR}/Pulumi.prod.yaml"
            echo -e "${GREEN}✓ Configuration copied from example${NC}"
            echo -e "${YELLOW}⚠ Please edit Pulumi.prod.yaml to customize your settings${NC}"
        fi
    fi
fi

echo ""
echo "════════════════════════════════════════════════════════════"
echo ""
echo -e "${GREEN}Setup complete!${NC}"
echo ""
echo "Next steps:"
echo ""
echo "  1. Review configuration:"
echo "     ${YELLOW}${PULUMI_CMD} config${NC}"
echo ""
echo "  2. Preview infrastructure changes:"
echo "     ${YELLOW}${PULUMI_CMD} preview${NC}"
echo ""
echo "  3. Deploy infrastructure:"
echo "     ${YELLOW}${PULUMI_CMD} up${NC}"
echo ""
echo "  4. After deployment, get kubeconfig:"
echo "     ${YELLOW}${PULUMI_CMD} stack output kubeconfig > kubeconfig.yaml${NC}"
echo "     ${YELLOW}export KUBECONFIG=\$(pwd)/kubeconfig.yaml${NC}"
echo ""
echo "  5. Verify cluster access:"
echo "     ${YELLOW}kubectl get nodes${NC}"
echo ""
echo "For more information, see PULUMI-README.md"
echo ""

# Ask if user wants to preview now
read -p "Would you like to preview the infrastructure now? [y/N] " preview_now
if [ "$preview_now" == "y" ] || [ "$preview_now" == "Y" ]; then
    echo ""
    ${PULUMI_CMD} preview
fi

