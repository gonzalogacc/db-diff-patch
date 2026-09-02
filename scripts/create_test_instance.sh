#!/usr/bin/env bash
#
# Creates a disposable Cloud SQL Postgres instance for the real-environment
# smoke test in README.md. Throwaway only - root password hardcoded.
#
# Needs both IPs: private for `diff`'s postgres_fdw hop (Cloud SQL rejects
# loopback connections, even same-instance), public so a machine outside
# the VPC can reach it via the Auth Proxy.
#
# Requires Private Services Access on NETWORK first - run
# scripts/enable_private_services_access.sh once per network.

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
