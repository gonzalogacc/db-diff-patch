#!/usr/bin/env bash
#
# Deletes a disposable Cloud SQL instance created by create_test_instance.sh
# (or any instance you pass by name). Deletes the persistent disk with it -
# there's no undo.

set -euo pipefail

# Set default instance name or take from first argument
INSTANCE_NAME=${1:-"test-pg-instance"}

echo "Deleting Cloud SQL instance: ${INSTANCE_NAME}..."

# Deletes the instance without prompting for interactive confirmation (--quiet)
gcloud sql instances delete "${INSTANCE_NAME}" --quiet

echo "Instance '${INSTANCE_NAME}' and its persistent disk have been deleted."
echo "Charges for this instance have stopped."
