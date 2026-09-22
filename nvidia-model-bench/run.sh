#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

# Live .env is 600 plaintext (root decrypts from secrets.sops.env). Do not sops as k.
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi
python3 -u server.py >> server.log 2>&1 &

echo "NVIDIA Model Responsiveness Benchmark app started at http://localhost:8585"
