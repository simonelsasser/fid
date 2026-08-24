#!/bin/bash

# Install fid to /usr/local/bin
# Requires sudo privileges

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cp "$SCRIPT_DIR/bin/fid" /usr/local/bin/fid
chmod +x /usr/local/bin/fid

echo "fid installed to /usr/local/bin/fid"