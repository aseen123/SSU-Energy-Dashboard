#!/bin/bash
# run_pipeline.sh — entrypoint for the Hostinger cron job.
# Use this as the Custom cron command:
#   0 6 * * * /home/u209446640/pipeline/run_pipeline.sh

set -u
cd "$(dirname "$0")" || exit 1

mkdir -p logs
LOG="logs/cron_$(date +%Y%m%d).log"

{
    echo "=========================================="
    echo "=== run @ $(date '+%Y-%m-%d %H:%M:%S') ==="
    echo "=========================================="
    /usr/bin/python3 master_pipeline.py
    EXIT=$?
    echo "=== exit code: $EXIT ==="
} >> "$LOG" 2>&1

exit $EXIT
