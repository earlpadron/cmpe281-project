#!/usr/bin/env bash
# Installs and provisions the AWS IoT Greengrass v2 Nucleus on a Raspberry Pi.
# Run this ONCE per Pi. Idempotent on re-run (will skip already-installed bits).
#
# Prereqs on the Pi:
#   * Debian/Ubuntu/Raspberry Pi OS (64-bit recommended)
#   * Internet access
#   * AWS credentials in the current shell with rights to create IAM roles,
#     IoT things, and the TES role -- this script provisions automatically with
#     the `--provision true` flag, which uses these creds.
#
# Usage:
#   AWS_REGION=us-east-1 ./bootstrap_pi.sh cmpe281-pi-01 cmpe281-edge-fleet
set -euo pipefail

THING_NAME="${1:-cmpe281-pi-01}"
THING_GROUP="${2:-cmpe281-edge-fleet}"
AWS_REGION="${AWS_REGION:-us-east-1}"
GG_INSTALL_DIR="/greengrass/v2"
GG_ROOT_USER="ggc_user"
GG_ROOT_GROUP="ggc_group"

echo ">>> Installing system dependencies..."
sudo apt-get update -y
sudo apt-get install -y \
    default-jdk-headless \
    python3 python3-pip python3-venv \
    unzip curl awscli \
    libjpeg-dev zlib1g-dev libatlas-base-dev libopenjp2-7

echo ">>> Creating Greengrass system user/group (${GG_ROOT_USER}:${GG_ROOT_GROUP})..."
sudo useradd --system --create-home "${GG_ROOT_USER}"   2>/dev/null || true
sudo groupadd --system "${GG_ROOT_GROUP}"               2>/dev/null || true

echo ">>> Downloading Greengrass v2 nucleus..."
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
curl -fsSL -o "${TMP}/gg.zip" \
    https://d2s8p88vqu9w66.cloudfront.net/releases/greengrass-nucleus-latest.zip
unzip -q "${TMP}/gg.zip" -d "${TMP}/GreengrassInstaller"

echo ">>> Provisioning Greengrass core (Thing=${THING_NAME}, Group=${THING_GROUP}, Region=${AWS_REGION})..."
sudo -E java -Droot="${GG_INSTALL_DIR}" -Dlog.store=FILE \
    -jar "${TMP}/GreengrassInstaller/lib/Greengrass.jar" \
    --aws-region "${AWS_REGION}" \
    --thing-name "${THING_NAME}" \
    --thing-group-name "${THING_GROUP}" \
    --component-default-user "${GG_ROOT_USER}:${GG_ROOT_GROUP}" \
    --provision true \
    --setup-system-service true \
    --deploy-dev-tools true

echo ">>> Verifying installation..."
sudo systemctl status greengrass --no-pager | head -n 20 || true

echo
echo "Greengrass nucleus installed under ${GG_INSTALL_DIR}."
echo "Next:"
echo "  1) On your dev machine, run scripts/publish_component.sh for both components."
echo "  2) Then run scripts/deploy.sh ${THING_GROUP}."
