#!/usr/bin/env bash
#
# Creates a disposable Cloud SQL Postgres instance. Use it for the
# real-environment smoke test in README.md. This instance is for testing
# only. The root password is hardcoded.
#
# The instance gets two IP addresses. `diff`'s postgres_fdw hop needs the
# private IP. Cloud SQL rejects a loopback connection there, even from an
# instance to itself. A client outside the VPC needs the public IP to
# reach the instance through the Auth Proxy.
#
# Run scripts/enable_private_services_access.sh once per network, before
# this script.
#
# This script uses the project set in your gcloud config. Run
# "gcloud config set project YOUR_PROJECT" first if needed.
#
# Usage: ./scripts/create_test_instance.sh [INSTANCE_NAME=test-pg-instance] [NETWORK=default]

set -euo pipefail

INSTANCE_NAME=${1:-"test-pg-instance"}
NETWORK=${2:-"default"}
REGION="us-central1"           # --region and --zone are mutually exclusive
POSTGRES_VERSION="POSTGRES_15"
TIER="db-f1-micro"             # Smallest, cheapest tier (Shared vCPU)
STORAGE_SIZE="10"              # GB - minimum allowed storage
ROOT_PASSWORD="MyTestPassword123!"

echo "Creating Cloud SQL instance: ${INSTANCE_NAME} (network: ${NETWORK})..."

# No --async: omitting it blocks until the instance is ready. --async takes
# no value in current gcloud, so --async=false errors out.
gcloud sql instances create "${INSTANCE_NAME}" \
    --database-version="${POSTGRES_VERSION}" \
    --tier="${TIER}" \
    --region="${REGION}" \
    --storage-size="${STORAGE_SIZE}" \
    --storage-type="HDD" \
    --root-password="${ROOT_PASSWORD}" \
    --no-deletion-protection \
    --network="${NETWORK}"

echo "Database created successfully!"
echo "------------------------------------"
echo "Instance Name : ${INSTANCE_NAME}"
echo "User          : postgres"
echo "Password      : ${ROOT_PASSWORD}"
echo "------------------------------------"
