#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR/backend"

# Activate virtual environment if present
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Inject SOPS secrets if available
if [ -f "../secrets.sops.env" ] && command -v sops >/dev/null 2>&1; then
    sops exec-env ../secrets.sops.env "python3 main.py" >> backend.log 2>&1 &
else
    python3 main.py >> backend.log 2>&1 &
fi

echo "Homelab Command Center running at http://localhost:8088"
