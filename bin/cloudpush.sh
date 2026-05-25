#!/usr/bin/env bash

################################################################
#
# Cloud Push
# cloudpush.sh
# Pushes fid registry to a Nextcloud shared folder
#
# Writes fid registry to temp file, uploads to Nextcloud
# Usage: ./cloudpush.sh <folderLink> [password]
#
################################################################

set -e

FOLDERLINK="$1"
PASSWORD="${2:-}"

# Validate inputs
if [[ -z "$FOLDERLINK" ]]; then
    echo "ERROR input"
    exit 1
fi

# Get the cache directory and create temp file
CACHE_DIR="${HOME}/fid/cache"
REGISTRY_FILE="$CACHE_DIR/fid-registry.csv"

# Write registry to temp file
if ! ~/GitHub/fid/bin/fid write "$REGISTRY_FILE"; then
    echo "ERROR writing registry"
    exit 1
fi

# Get the base URL and token from the folder link
if [[ "$FOLDERLINK" == *"index.php"* ]]; then
    CLOUDURL="${FOLDERLINK%/index.php/s/*}"
else
    CLOUDURL="${FOLDERLINK%/s/*}"
fi

# Extract token
FOLDERTOKEN="${FOLDERLINK##*/s/}"
FOLDERTOKEN="${FOLDERTOKEN%\?*}"

if [[ -z "$CLOUDURL" || -z "$FOLDERTOKEN" ]]; then
    echo "ERROR retrieving cloud url"
    rm -f "$REGISTRY_FILE"
    exit 1
fi

# Get basename of the registry file
BASENAME=$(basename "$REGISTRY_FILE")

# Build curl command for file upload
CURL_CMD="curl -s -T '$REGISTRY_FILE' -u '$FOLDERTOKEN:$PASSWORD' -H 'X-Requested-With: XMLHttpRequest'"

# Add upload path
CURL_CMD="$CURL_CMD '$CLOUDURL/public.php/webdav/$BASENAME'"

# Execute curl
if eval "$CURL_CMD" >/dev/null 2>&1; then
    # Build download URL
    ENCODED_FILENAME=$(echo "$BASENAME" | sed 's/ /%20/g')
    DOWNLOAD_URL="$CLOUDURL/index.php/s/$FOLDERTOKEN/download?path=/&files=$ENCODED_FILENAME"
    
    # Delete the temp file
    rm -f "$REGISTRY_FILE"
    
    # Output download URL
    echo "$DOWNLOAD_URL"
    exit 0
else
    rm -f "$REGISTRY_FILE"
    exit 1
fi
