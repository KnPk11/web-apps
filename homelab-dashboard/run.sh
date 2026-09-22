#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR/backend"

# Activate virtual environment if present
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Live .env is 600 plaintext next to secrets.sops.env (root decrypts). Do not sops as k.
if [ -f "$DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$DIR/.env"
    set +a
fi
python3 main.py >> backend.log 2>&1 &

echo "Homelab Command Center running at http://localhost:8088"
