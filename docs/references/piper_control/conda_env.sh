#!/bin/bash

# Script to create a conda environment with:
# -  Python 3.10
# -  ipykernel
# -  piper_control (editable install)
# -  can-utils (installed as system-wide package)
#
# Usage:
#   ./conda_env.sh
# This script assumes that conda is already installed and available in the PATH.
# Check if conda is installed with:
#   conda --version
#
# This script can be used to create a conda env for local development or testing.

ENV_NAME="piper_control"
YAML_FILE="piper_control.yaml"


# Install can-utils for CAN bus communication.
if command -v candump >/dev/null 2>&1; then
  echo "can-utils is already installed."
else
  if sudo apt install can-utils; then
    echo "can-utils installed successfully."
  else
    echo "Failed to install can-utils."
    exit 1
  fi
fi

# Create the conda environment
if conda env update -n "$ENV_NAME" --file "$YAML_FILE" --prune; then
  echo "Conda environment '$ENV_NAME' created successfully."
else
  echo "Failed to create conda environment '$ENV_NAME'."
  exit 1
fi
