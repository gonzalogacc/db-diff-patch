#!/usr/bin/env bash
#
# Deletes a Cloud SQL instance and its disk. There is no undo.
#
# This script uses the project set in your gcloud config. Run
# "gcloud config set project YOUR_PROJECT" first if needed.
#
# Usage: ./scripts/delete_test_instance.sh [INSTANCE_NAME=test-pg-instance]

set -euo pipefail

INSTANCE_NAME=${1:-"test-pg-instance"}

echo "Deleting Cloud SQL instance: ${INSTANCE_NAME}..."

# Deletes the instance without prompting for interactive confirmation (--quiet)
gcloud sql instances delete "${INSTANCE_NAME}" --quiet

echo "Instance '${INSTANCE_NAME}' and its persistent disk have been deleted."
echo "Charges for this instance have stopped."
