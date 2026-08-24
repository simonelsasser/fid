#!/bin/bash

# Install fid CLI and server to /usr/local/bin
# Requires sudo privileges

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

sudo cp "$SCRIPT_DIR/bin/fid" /usr/local/bin/fid
sudo cp "$SCRIPT_DIR/bin/fid_cli.py" /usr/local/bin/fid_cli.py
sudo cp "$SCRIPT_DIR/bin/fid_api.py" /usr/local/bin/fid_api.py

sudo chmod +x /usr/local/bin/fid
sudo chmod +x /usr/local/bin/fid_cli.py
sudo chmod +x /usr/local/bin/fid_api.py

echo "✓ fid installed to /usr/local/bin/"
echo "  - fid (CLI client)"
echo "  - fid_cli.py (core module)"  
echo "  - fid_api.py (HTTP API server)"
echo ""
echo "To start the server:"
echo "  fid_api.py --port 8080"