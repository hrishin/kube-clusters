#!/bin/bash
# Setup script for Pulumi EKS infrastructure

set -e

echo "Setting up Pulumi EKS infrastructure..."

PROJECT_DIR="clusters/eks-alpha/infr"

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is required but not installed."
    exit 1
fi

# Check if Pulumi is installed
if ! command -v pulumi &> /dev/null; then
    echo "Error: Pulumi is not installed."
    echo "Please install Pulumi: curl -fsSL https://get.pulumi.com | sh"
    exit 1
fi

# Create virtual environment
echo "Creating Python virtual environment..."
python3 -m venv venv

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "Setup complete!"
echo ""
echo "Next steps:"
echo "1. Activate the virtual environment: source venv/bin/activate"
echo "2. Login to Pulumi: pulumi login"
echo "3. Create a new stack: pulumi -C ${PROJECT_DIR} stack init dev"
echo "4. Configure the stack (optional, defaults are set in Pulumi.yaml):"
echo "   pulumi -C ${PROJECT_DIR} config set aws:region eu-west-2"
echo "   pulumi -C ${PROJECT_DIR} config set eks-pulumi:cluster_name infra-cluster"
echo "5. Preview changes: pulumi -C ${PROJECT_DIR} preview"
echo "6. Deploy infrastructure: pulumi -C ${PROJECT_DIR} up"
echo ""

