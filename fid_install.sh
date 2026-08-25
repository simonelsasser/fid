#!/bin/bash

# Install fid CLI and server to /usr/local/bin
# Requires sudo privileges

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

sudo cp "$SCRIPT_DIR/bin/fid" /usr/local/bin/fid
sudo cp "$SCRIPT_DIR/bin/fid.py" /usr/local/bin/fid.py
sudo cp "$SCRIPT_DIR/bin/fid_api.py" /usr/local/bin/fid_api.py
sudo cp "$SCRIPT_DIR/bin/fid_git.py" /usr/local/bin/fid_git.py

sudo chmod +x /usr/local/bin/fid
sudo chmod +x /usr/local/bin/fid.py
sudo chmod +x /usr/local/bin/fid_api.py
sudo chmod +x /usr/local/bin/fid_git.py

echo "✓ fid installed to /usr/local/bin/"
echo "  - fid (CLI client)"
echo "  - fid.py (module symlink)"  
echo "  - fid_api.py (HTTP API server)"
echo "  - fid_git.py (Git filter driver)"
echo ""
echo "To start the server:"
echo "  fid_api.py --port 8080"