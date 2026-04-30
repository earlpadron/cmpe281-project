#!/usr/bin/env bash
# Lifecycle 'Run' wrapper for the cmpe281.decision_engine Greengrass component.
# Greengrass injects the runtime config via environment variables (see recipe).
set -euo pipefail

ARTIFACT_DIR="${ARTIFACT_DIR:-$(cd "$(dirname "$0")" && pwd)}"
APP_DIR="${ARTIFACT_DIR}/backend"
VENV_DIR="${ARTIFACT_DIR}/.venv"

cd "${APP_DIR}"

# Use the venv created in the Install lifecycle.
export S3_BUCKET_NAME="your-s3-bucket-name"
export S3_BUCKET_REGION="us-west-1"
export LYAPUNOV_STATE_FILE="/greengrass/v2/work/cmpe281.decision_engine/lyapunov_queue.json"
exec "${VENV_DIR}/bin/python3" "${ARTIFACT_DIR}/backend/main.py"
