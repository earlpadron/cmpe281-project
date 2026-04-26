#!/usr/bin/env bash
# Lifecycle 'Run' wrapper for the cmpe281.decision_engine Greengrass component.
# Greengrass injects the runtime config via environment variables (see recipe).
set -euo pipefail

ARTIFACT_DIR="${ARTIFACT_DIR:-$(cd "$(dirname "$0")" && pwd)}"
APP_DIR="${ARTIFACT_DIR}/backend"
VENV_DIR="${ARTIFACT_DIR}/.venv"

cd "${APP_DIR}"

# Use the venv created in the Install lifecycle.
exec "${VENV_DIR}/bin/uvicorn" main:app \
    --host "${API_HOST:-0.0.0.0}" \
    --port "${API_PORT:-8000}" \
    --workers 1
