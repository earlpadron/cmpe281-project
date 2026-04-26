#!/usr/bin/env bash
# Pushes a Greengrass deployment that installs the cmpe281.decision_engine +
# cmpe281.edge_resizer components onto every device in the given Thing Group.
#
# Usage:
#   ./deploy.sh cmpe281-edge-fleet
#
# Optional env:
#   DECISION_ENGINE_VERSION  (default 1.0.0)
#   EDGE_RESIZER_VERSION     (default 1.0.0)
#   AWS_REGION               (default us-east-1)
set -euo pipefail

THING_GROUP="${1:?Thing group name (e.g. cmpe281-edge-fleet) required}"
AWS_REGION="${AWS_REGION:-us-east-1}"
DECISION_ENGINE_VERSION="${DECISION_ENGINE_VERSION:-1.0.0}"
EDGE_RESIZER_VERSION="${EDGE_RESIZER_VERSION:-1.0.0}"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
TARGET_ARN="arn:aws:iot:${AWS_REGION}:${ACCOUNT_ID}:thinggroup/${THING_GROUP}"

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
DEPLOY_JSON="${WORK}/deployment.json"

cat >"${DEPLOY_JSON}" <<JSON
{
  "cmpe281.edge_resizer": {
    "componentVersion": "${EDGE_RESIZER_VERSION}",
    "configurationUpdate": {}
  },
  "cmpe281.decision_engine": {
    "componentVersion": "${DECISION_ENGINE_VERSION}",
    "configurationUpdate": {}
  },
  "aws.greengrass.Cli": {
    "componentVersion": "2.13.0"
  }
}
JSON

echo ">>> Creating deployment for ${TARGET_ARN}..."
aws greengrassv2 create-deployment \
    --region "${AWS_REGION}" \
    --target-arn "${TARGET_ARN}" \
    --deployment-name "cmpe281-edge-$(date +%Y%m%d-%H%M%S)" \
    --components "file://${DEPLOY_JSON}"

echo
echo "Deployment submitted. Watch progress with:"
echo "  aws greengrassv2 list-effective-deployments --core-device-thing-name <thing-name>"
echo "  ssh pi 'sudo tail -f /greengrass/v2/logs/cmpe281.decision_engine.log /greengrass/v2/logs/cmpe281.edge_resizer.log'"
