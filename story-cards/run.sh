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
python3 -u review_worker.py >> worker.log 2>&1 &

# Start main web app
python3 -u app.py >> app.log 2>&1 &

echo "Story Cards application started."
