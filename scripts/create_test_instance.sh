#!/usr/bin/env bash
#
# Creates a disposable Cloud SQL for Postgres instance to use as SOURCE for
# the real-environment smoke test in README.md. Not for anything you care
# about keeping - the root password below is hardcoded on purpose for
# throwaway test use, not production.
#
# Branching happens at the database level (CREATE DATABASE ... TEMPLATE,
# all on this same instance) - but Cloud SQL still rejects postgres_fdw
# connecting to the loopback address ("connection to the loopback address
# is not allowed"), even for an instance connecting to itself. So `diff`'s
# FDW hop between the base/modify databases has to target this instance's
# own private IP, which is why this still requests one via --network.
#
# Public IP is also requested (the default - no --no-assign-ip) so a
# machine outside the VPC (e.g. your laptop, running these scripts or
# `db-differ`) can reach this instance at all through the Auth Proxy.
#
# Prerequisite this script does NOT set up for you: --network requires
# Private Services Access already configured for that VPC (a one-time,
# project-level thing). Run scripts/enable_private_services_access.sh once
# per network before this script. Without it, this command fails with a
# clear gcloud error telling you to set up the VPC peering first.

set -euo pipefail

# --- CONFIGURATION ---
INSTANCE_NAME=${1:-"test-pg-instance"}
NETWORK=${2:-"default"}
REGION="us-central1"           # --region and --zone are mutually exclusive
                                # for `sql instances create` - a specific
                                # zone implies its region, so we just pick
                                # the region and let Cloud SQL choose a zone.
POSTGRES_VERSION="POSTGRES_15"
TIER="db-f1-micro"             # Smallest, cheapest tier (Shared vCPU)
STORAGE_SIZE="10"              # GB - minimum allowed storage
ROOT_PASSWORD="MyTestPassword123!"

echo "Creating Cloud SQL instance: ${INSTANCE_NAME} (network: ${NETWORK})..."

gcloud sql instances create "${INSTANCE_NAME}" \
    --database-version="${POSTGRES_VERSION}" \
    --tier="${TIER}" \
    --region="${REGION}" \
    --storage-size="${STORAGE_SIZE}" \
    --storage-type="HDD" \
    --root-password="${ROOT_PASSWORD}" \
    --no-deletion-protection \
    --network="${NETWORK}"
    # No --async flag: omitting it is what makes this call block until the
    # instance is ready, which is what we want here. In current gcloud
    # --async is a plain boolean flag (present = return immediately) - it
    # does not take a value, so `--async=false` errors out.

echo "Database created successfully!"
echo "------------------------------------"
echo "Instance Name : ${INSTANCE_NAME}"
echo "User          : postgres"
echo "Password      : ${ROOT_PASSWORD}"
echo "------------------------------------"
