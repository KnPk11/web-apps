#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

# Start worker with SOPS secrets injection if available
if [ -f "secrets.sops.env" ] && command -v sops >/dev/null 2>&1; then
    sops exec-env secrets.sops.env "python3 -u review_worker.py" >> worker.log 2>&1 &
else
    python3 -u review_worker.py >> worker.log 2>&1 &
fi

# Start main web app
python3 -u app.py >> app.log 2>&1 &

echo "Story Cards application started."
