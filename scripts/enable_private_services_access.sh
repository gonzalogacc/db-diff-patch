#!/usr/bin/env bash
#
# Required once per network before create_test_instance.sh. Reserves an IP
# range for Google-managed services and peers it with the VPC - what makes
# --network=... (private IP) work on `gcloud sql instances create`.
#
# Safe to re-run.
#
# Usage: ./scripts/enable_private_services_access.sh [NETWORK]

set -euo pipefail

NETWORK=${1:-"default"}
RANGE_NAME="google-managed-services-${NETWORK}"

echo "Enabling the Service Networking and Compute Engine APIs (safe if already enabled)..."
gcloud services enable servicenetworking.googleapis.com compute.googleapis.com

if gcloud compute addresses describe "${RANGE_NAME}" --global &>/dev/null; then
    echo "IP range '${RANGE_NAME}' already reserved, skipping."
else
    echo "Reserving IP range '${RANGE_NAME}' for VPC peering on network '${NETWORK}'..."
    gcloud compute addresses create "${RANGE_NAME}" \
        --global \
        --purpose=VPC_PEERING \
        --prefix-length=16 \
        --network="${NETWORK}"
fi

echo "Connecting VPC peering for Private Services Access on network '${NETWORK}'..."
gcloud services vpc-peerings connect \
    --service=servicenetworking.googleapis.com \
    --ranges="${RANGE_NAME}" \
    --network="${NETWORK}"

echo "Private Services Access is set up for network '${NETWORK}'."
echo "You can now run create_test_instance.sh with --network=${NETWORK}."
