#!/usr/bin/env bash

set -e

echo "Installing fid Finder integrations..."

SERVICES="$HOME/Library/Services"
mkdir -p "$SERVICES"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
FID_PATH="$( cd "$SCRIPT_DIR/../bin" && pwd )/fid"


install_workflow() {

NAME="$1"

SRC="$SCRIPT_DIR/templates/$NAME.workflow"
DEST="$SERVICES/$NAME.workflow"

cp -R "$SRC" "$DEST"

DOC="$DEST/Contents/document.wflow"

# patch only Automator script file
LC_ALL=C sed -i '' \
"s|__FID_PATH__|$FID_PATH|g" \
"$DOC"

}


install_workflow "Register with fid"
install_workflow "Copy fid"

echo
echo "Installed successfully."
echo "Restart Finder:"
echo "killall Finder"