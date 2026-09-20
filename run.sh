#!/bin/bash
# Pulls fresh data (skipping sources whose cache is still fresh) and starts
# the local dashboard at http://localhost:5050
set -e
cd "$(dirname "$0")"
source venv/bin/activate
python refresh_all.py
echo ""
echo "Starting dashboard at http://localhost:5050 (Ctrl+C to stop)"
python app.py
