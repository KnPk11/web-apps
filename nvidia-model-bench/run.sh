#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

# Start benchmark server with SOPS secrets injection if available
if [ -f "secrets.sops.env" ] && command -v sops >/dev/null 2>&1; then
    sops exec-env secrets.sops.env "python3 -u server.py" >> server.log 2>&1 &
else
    python3 -u server.py >> server.log 2>&1 &
fi

echo "NVIDIA Model Responsiveness Benchmark app started at http://localhost:8585"
