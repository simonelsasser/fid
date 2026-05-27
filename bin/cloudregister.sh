#!/usr/bin/env bash

FIDPATH="$HOME/fid/bin/"
FIDPATH="$HOME/GitHub/fid/bin/"

################################################################
#
# Cloud Register
# cloudregister.sh
# Registers one or more files to a Nextcloud shared folder with fid backend
#
# Registers files to fid, uploads to Nextcloud, registers remote URLs to fid
# Usage: ./cloudregister.sh <folderLink> [password] <file> [<file> ...]
#
################################################################

set -e

# Parse arguments: FOLDERLINK is first, optional PASSWORD is second, rest are FILES
FOLDERLINK="$1"
shift

# Check if second argument looks like a password (doesn't exist or doesn't start with /)
if [[ $# -gt 0 && ! "$1" =~ ^/ ]]; then
    PASSWORD="$1"
    shift
else
    PASSWORD=""
fi

# Remaining arguments are files
FILES=("$@")

# Validate inputs
if [[ -z "$FOLDERLINK" ]]; then
    echo "ERROR input"
    exit 1
fi

if [[ ${#FILES[@]} -eq 0 ]]; then
    echo "ERROR no files specified"
    exit 1
fi

# Get the base URL and token from the folder link (do this once)
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
    exit 1
fi

# Process each file
for FILE in "${FILES[@]}"; do
    # Validate file exists
    if [[ ! -f "$FILE" ]]; then
        echo "ERROR file does not exist: $FILE"
        continue
    fi

    # Register file with fid and capture the FID
    FID=$( $FIDPATH/fid register "$FILE" )
    if [[ -z "$FID" ]]; then
        echo "ERROR registering: $FILE"
        continue
    fi
    echo $FID $FILE
    FOLDERNAME="$FID"

    # Get basename of file
    BASENAME=$(basename "$FILE")

    # Create the folder on WebDAV first
    TARGET_FOLDER=$(echo "$FOLDERNAME" | sed 's/ /%20/g')
    curl -s -X MKCOL -u "$FOLDERTOKEN:$PASSWORD" -H 'X-Requested-With: XMLHttpRequest' "$CLOUDURL/public.php/webdav/$TARGET_FOLDER" >/dev/null 2>&1

    # Build curl command for file upload
    CURL_CMD="curl -s -T '$FILE' -u '$FOLDERTOKEN:$PASSWORD' -H 'X-Requested-With: XMLHttpRequest'"

    # Add target folder to upload path
    CURL_CMD="$CURL_CMD '$CLOUDURL/public.php/webdav/$TARGET_FOLDER/$BASENAME'"

    # Execute curl
    if eval "$CURL_CMD" >/dev/null 2>&1; then
        # Build download URL
        ENCODED_TARGET=$(echo "$FOLDERNAME" | sed 's/\//%2f/g ; s/ /%20/g')
        ENCODED_FILENAME=$(echo "$BASENAME" | sed 's/ /%20/g')
        DOWNLOAD_URL="$CLOUDURL/index.php/s/$FOLDERTOKEN/download?path=/$ENCODED_TARGET&files=$ENCODED_FILENAME"
        
        # Register the remote URL with fid
        $FIDPATH/fid add "$FID" "$DOWNLOAD_URL"
        
        # Output comma-separated FID and DOWNLOAD_URL
        echo "$FID,$DOWNLOAD_URL"
    else
        echo "ERROR uploading: $FILE"
    fi
done
