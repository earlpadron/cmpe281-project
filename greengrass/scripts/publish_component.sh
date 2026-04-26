#!/usr/bin/env bash
# Packages a Greengrass component, uploads its artifact to S3, and registers the
# version with AWS IoT Greengrass V2. Run once per component, per version bump.
#
# Usage:
#   ./publish_component.sh cmpe281.edge_resizer    1.0.0
#   ./publish_component.sh cmpe281.decision_engine 1.0.0
#
# Required env:
#   ARTIFACT_BUCKET  -- S3 bucket that hosts component zips
#   AWS_REGION       -- defaults to us-east-1
set -euo pipefail

COMPONENT_NAME="${1:?component name (e.g. cmpe281.edge_resizer) required}"
VERSION="${2:?version (e.g. 1.0.0) required}"
AWS_REGION="${AWS_REGION:-us-east-1}"
ARTIFACT_BUCKET="${ARTIFACT_BUCKET:?ARTIFACT_BUCKET env var required}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_ROOT="$(cd "${ROOT}/.." && pwd)"
COMPONENT_DIR="${ROOT}/components/${COMPONENT_NAME}"
ARTIFACTS_DIR="${COMPONENT_DIR}/artifacts"

if [[ ! -d "${COMPONENT_DIR}" ]]; then
    echo "Component directory not found: ${COMPONENT_DIR}" >&2
    exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
STAGE="${WORK}/${COMPONENT_NAME}"
mkdir -p "${STAGE}"

echo ">>> Staging artifacts for ${COMPONENT_NAME}..."
cp -R "${ARTIFACTS_DIR}/." "${STAGE}/"

# Both components depend on the shared resize core. Copy it in as resize_lib.py
# so each component is self-contained at runtime.
cp "${PROJECT_ROOT}/backend/lib/resize.py" "${STAGE}/resize_lib.py"

# The decision-engine component additionally needs the entire backend/ tree.
if [[ "${COMPONENT_NAME}" == "cmpe281.decision_engine" ]]; then
    rsync -a \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        --exclude '.venv' \
        "${PROJECT_ROOT}/backend/" "${STAGE}/backend/"
fi

ZIP="${WORK}/${COMPONENT_NAME}.zip"
echo ">>> Zipping -> ${ZIP}"
( cd "${STAGE}" && zip -qr "${ZIP}" . )

S3_KEY="components/${COMPONENT_NAME}/${VERSION}/${COMPONENT_NAME}.zip"
S3_URI="s3://${ARTIFACT_BUCKET}/${S3_KEY}"
echo ">>> Uploading to ${S3_URI}"
aws s3 cp "${ZIP}" "${S3_URI}" --region "${AWS_REGION}"

# Materialize the recipe by substituting __ARTIFACT_BUCKET__ and version.
RECIPE_SRC="${COMPONENT_DIR}/recipe.yaml"
RECIPE_OUT="${WORK}/recipe.yaml"
sed \
    -e "s|__ARTIFACT_BUCKET__|${ARTIFACT_BUCKET}|g" \
    -e "s|^ComponentVersion:.*|ComponentVersion: '${VERSION}'|" \
    "${RECIPE_SRC}" > "${RECIPE_OUT}"

echo ">>> Registering component version with Greengrass V2..."
aws greengrassv2 create-component-version \
    --region "${AWS_REGION}" \
    --inline-recipe "fileb://${RECIPE_OUT}"

echo
echo "OK. Published ${COMPONENT_NAME}@${VERSION}."
