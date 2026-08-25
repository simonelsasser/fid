#!/usr/bin/env python3
"""
Fid Git Filter Driver

Provides clean/smudge filters for git to automatically upload/download
large files to/from a fid server.

Usage:
    git config filter.fid.clean "/path/to/fid_git.py clean"
    git config filter.fid.smudge "/path/to/fid_git.py smudge"
    git config filter.fid.required "true"
"""

import os
import sys
import json
import subprocess
import hashlib
import urllib.parse

# Script directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Add to path to import fid module
sys.path.insert(0, SCRIPT_DIR)

# Import register_single from fid
from fid import register_single


def find_config(repo_root):
    """Find and load .fidconfig from repo root."""
    config_path = os.path.join(repo_root, ".fidconfig")
    
    if not os.path.exists(config_path):
        return None
    
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
        return config
    except Exception as e:
        print(f"fid-git: error reading .fidconfig: {e}", file=sys.stderr)
        return None


def get_git_config(key, default=None):
    """Get git config value."""
    try:
        result = subprocess.run(
            ["git", "config", key],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return default


def get_auth_key(config, repo_root):
    """Get authentication key, preferring .git/config over .fidconfig."""
    # Try git config first (local override)
    auth_key = get_git_config("filter.fid.auth_key")
    if auth_key:
        return auth_key
    
    # Fall back to .fidconfig
    if config and "auth_key" in config:
        return config["auth_key"]
    
    return None


def get_server_url(config):
    """Get server URL from config."""
    # Try git config first
    server = get_git_config("filter.fid.server")
    if server:
        return server
    
    # Fall back to .fidconfig
    if config and "server" in config:
        return config["server"]
    
    return None


def get_repo_root():
    """Get git repository root directory."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None


def upload_to_server(content, filename, fid, config, auth_key):
    """Upload content to fid server with fid and filename.
    
    Args:
        content: File content (bytes)
        filename: Original filename (string)
        fid: Pre-calculated fid (string, 22 chars)
        config: Server configuration
        auth_key: Authentication key (optional)
    
    Returns:
        fid on success
    
    Raises:
        SystemExit: On upload failure
    """
    import urllib.request
    
    server_url = get_server_url(config)
    if not server_url:
        print("fid-git: error: no server configured", file=sys.stderr)
        sys.exit(1)
    
    # Ensure server URL doesn't have trailing slash
    server_url = server_url.rstrip("/")
    
    # Upload to server with fid and filename as query parameters
    upload_url = f"{server_url}/upload?fid={fid}&filename={urllib.parse.quote(filename or '')}"
    
    try:
        req = urllib.request.Request(
            upload_url,
            data=content,
            method="POST"
        )
        req.add_header("Content-Type", "application/octet-stream")
        
        # Add auth header if available
        if auth_key:
            req.add_header("Authorization", f"Bearer {auth_key}")
        
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode())
            
            if result.get("status") == "uploaded":
                return result.get("fid")
            elif result.get("status") == "duplicate":
                return result.get("fid")
            else:
                print(f"fid-git: upload failed: {result}", file=sys.stderr)
                sys.exit(1)
                
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        print(f"fid-git: upload failed (HTTP {e.code}): {error_body}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"fid-git: upload failed: {e}", file=sys.stderr)
        sys.exit(1)


def download_from_server(fid, config, auth_key):
    """Download content from fid server."""
    import urllib.request
    
    server_url = get_server_url(config)
    if not server_url:
        print("fid-git: error: no server configured", file=sys.stderr)
        sys.exit(1)
    
    # Ensure server URL doesn't have trailing slash
    server_url = server_url.rstrip("/")
    
    # Download from server
    download_url = f"{server_url}/?fid={fid}"
    
    try:
        req = urllib.request.Request(download_url)
        
        # Add auth header if available
        if auth_key:
            req.add_header("Authorization", f"Bearer {auth_key}")
        
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
            
    except Exception as e:
        print(f"fid-git: download failed for {fid}: {e}", file=sys.stderr)
        # Return None to indicate failure, but don't exit - let smudge continue
        return None


def validate_fid(fid_string):
    """Validate that string is a valid fid format."""
    if not fid_string.startswith("fid://"):
        return False
    
    fid = fid_string[6:]  # Remove "fid://" prefix
    
    # FID should be 22 base62 characters
    if len(fid) != 22:
        return False
    
    base62_chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    if not all(c in base62_chars for c in fid):
        return False
    
    return True


def get_fid_for_file(filepath):
    """Get fid for a file using fid id --register logic.
    
    Tries to get existing fid for the file path.
    If not found, registers the file and returns the new fid.
    """
    import subprocess
    
    try:
        # Run: fid id --register <filepath>
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPT_DIR, "fid"), "id", "--register", filepath],
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout for large files
        )
        
        if result.returncode == 0 and result.stdout.strip():
            fid = result.stdout.strip()
            # Validate it's a proper fid
            if validate_fid(f"fid://{fid}"):
                return fid
        
        return None
        
    except Exception as e:
        print(f"fid-git: error getting fid for {filepath}: {e}", file=sys.stderr)
        return None


def check_fid_on_server(fid, config, auth_key):
    """Check if fid exists on server via /resolve endpoint.
    
    Returns True if fid exists on server, False otherwise.
    """
    import urllib.request
    
    server_url = get_server_url(config)
    if not server_url:
        return False
    
    server_url = server_url.rstrip("/")
    resolve_url = f"{server_url}/resolve?fid={fid}"
    
    try:
        req = urllib.request.Request(resolve_url)
        
        if auth_key:
            req.add_header("Authorization", f"Bearer {auth_key}")
        
        with urllib.request.urlopen(req, timeout=30) as resp:
            # 200 OK means fid exists
            return resp.status == 200
            
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        print(f"fid-git: server error checking fid: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"fid-git: error checking fid on server: {e}", file=sys.stderr)
        return False


def resolve_fid_locally(fid):
    """Resolve fid to local path using fid resolve.
    
    Returns local file path if found, None otherwise.
    """
    import subprocess
    
    try:
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPT_DIR, "fid"), "resolve", f"fid://{fid}"],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0 and result.stdout.strip():
            path = result.stdout.strip()
            if os.path.exists(path):
                return path
        
        return None
        
    except Exception as e:
        print(f"fid-git: error resolving fid locally: {e}", file=sys.stderr)
        return None


def copy_file(src, dst):
    """Copy file from src to dst, creating directories as needed."""
    import shutil
    
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        return True
    except Exception as e:
        print(f"fid-git: error copying file: {e}", file=sys.stderr)
        return False


def clean_filter(filename):
    """
    Clean filter: get fid for file, upload to server with fid+filename, write fid pointer.
    
    Git calls this when adding files to the index.
    
    Workflow:
    1. Get fid for file (calculate or retrieve from DB)
    2. Read file content
    3. Upload to server with fid and filename as query parameters
    4. Server validates fid matches content, checks for duplicates
    5. Output fid://... pointer
    """
    # Get repo root and config
    repo_root = get_repo_root()
    if not repo_root:
        # Not in a git repo, pass through
        sys.stdout.buffer.write(sys.stdin.buffer.read())
        return
    
    config = find_config(repo_root)
    if not config:
        # No .fidconfig, pass through
        sys.stdout.buffer.write(sys.stdin.buffer.read())
        return
    
    auth_key = get_auth_key(config, repo_root)
    
    # Get absolute path of file being added
    if filename and not os.path.isabs(filename):
        filepath = os.path.join(repo_root, filename)
    else:
        filepath = filename
    
    # Step 1: Get fid for file (from DB or register)
    fid = get_fid_for_file(filepath) if filepath else None
    
    if not fid:
        # Fallback: read content and calculate fid
        content = sys.stdin.buffer.read()
        import hashlib
        md5_hash = hashlib.md5(content)
        from fid import base62_encode
        fid = base62_encode(md5_hash.digest())
        
        # Upload with calculated fid
        upload_to_server(content, filename, fid, config, auth_key)
    else:
        # Read content and upload with known fid
        content = sys.stdin.buffer.read()
        upload_to_server(content, filename, fid, config, auth_key)
    
    # Write fid pointer to stdout
    pointer = f"fid://{fid}\n".encode()
    sys.stdout.buffer.write(pointer)


def smudge_filter(target_path=None):
    """
    Smudge filter: resolve fid locally or download from server, write content to stdout.
    
    Git calls this when checking out files from the index.
    
    Workflow:
    1. Parse fid://... from stdin
    2. Try fid resolve locally
    3. If found: copy from local path
    4. If not found: download from server
    5. Fail loudly with clear error if all attempts fail
    
    Always overwrites target file - this is intentional to restore original version.
    """
    # Read fid pointer from stdin
    content = sys.stdin.read().strip()
    
    # Check if it's a valid fid pointer
    if not validate_fid(content):
        # Not a fid pointer, pass through as-is
        sys.stdout.write(content + "\n")
        return
    
    fid = content[6:]  # Remove "fid://" prefix
    
    # Get repo root
    repo_root = get_repo_root()
    if not repo_root:
        # Not in a git repo, can't resolve
        print(f"fid-git: ERROR:NOT_IN_REPO - cannot resolve {fid}", file=sys.stderr)
        # Output empty (file will be missing)
        return
    
    config = find_config(repo_root)
    
    # Step 2: Try fid resolve locally
    local_path = resolve_fid_locally(fid)
    
    if local_path and os.path.exists(local_path):
        # Step 3: Copy from local path
        try:
            with open(local_path, "rb") as f:
                file_content = f.read()
            sys.stdout.buffer.write(file_content)
            return
        except Exception as e:
            print(f"fid-git: ERROR:LOCAL_READ_FAILED - {local_path}: {e}", file=sys.stderr)
            # Fall through to download
    
    # Step 4: Download from server
    if not config:
        print(f"fid-git: ERROR:NO_FIDCONFIG - cannot download {fid} (no .fidconfig found)", file=sys.stderr)
        # Output empty (file will be missing)
        return
    
    # Check if server URL is configured
    server_url = get_server_url(config)
    if not server_url:
        print(f"fid-git: ERROR:NO_SERVER_URL - cannot download {fid} (server not configured in .fidconfig)", file=sys.stderr)
        # Output empty (file will be missing)
        return
    
    auth_key = get_auth_key(config, repo_root)
    downloaded = download_from_server(fid, config, auth_key)
    
    if downloaded is None:
        # Download failed
        print(f"fid-git: ERROR:DOWNLOAD_FAILED - could not download {fid} from {server_url}", file=sys.stderr)
        print(f"fid-git: HINT: Check that server is running and fid exists: curl {server_url}/resolve?fid={fid}", file=sys.stderr)
        # Output empty (file will be missing)
        return
    
    # Register downloaded file locally (so future resolves work)
    # Save to cache directory
    cache_dir = os.path.join(repo_root, ".fid", "cache", fid)
    os.makedirs(cache_dir, exist_ok=True)
    
    # We need a filename - use fid as name
    cache_path = os.path.join(cache_dir, fid)
    
    try:
        with open(cache_path, "wb") as f:
            f.write(downloaded)
        
        # Register in fid database
        register_single(cache_path)
        
    except Exception as e:
        print(f"fid-git: ERROR:REGISTER_FAILED - could not register downloaded file: {e}", file=sys.stderr)
        # Continue anyway - we still have the content
    
    # Write content to stdout (git will write to working directory)
    sys.stdout.buffer.write(downloaded)


def git_init():
    """
    Initialize git filter configuration for current repository.
    
    Creates .fidconfig template and configures git filter settings.
    """
    repo_root = get_repo_root()
    if not repo_root:
        print("fid-git: error: not in a git repository", file=sys.stderr)
        sys.exit(1)
    
    # Create .fidconfig if it doesn't exist
    config_path = os.path.join(repo_root, ".fidconfig")
    if not os.path.exists(config_path):
        template = {
            "server": "http://myserver.domain.com:8080",
            "auth_key": "your-auth-token"
        }
        with open(config_path, "w") as f:
            json.dump(template, f, indent=2)
        print(f"Created {config_path}")
        print("  Edit this file with your server URL and auth token")
    else:
        print(f"{config_path} already exists")
    
    # Configure git filter
    script_path = os.path.abspath(__file__)
    
    commands = [
        ("filter.fid.clean", f"{script_path} clean"),
        ("filter.fid.smudge", f"{script_path} smudge"),
        ("filter.fid.required", "true"),
    ]
    
    print("\nConfiguring git filter...")
    for key, value in commands:
        subprocess.run(["git", "config", key, value], check=True)
        print(f"  Set {key} = {value}")
    
    # Create example .gitattributes if it doesn't exist
    gitattributes_path = os.path.join(repo_root, ".gitattributes")
    if not os.path.exists(gitattributes_path):
        with open(gitattributes_path, "w") as f:
            f.write("# Fid filter for large files\n")
            f.write("# Uncomment or add patterns for files to manage with fid\n")
            f.write("#\n")
            f.write("# *.raw filter=fid\n")
            f.write("# *.bin filter=fid\n")
            f.write("# data/** filter=fid\n")
            f.write("# models/** filter=fid\n")
        print(f"\nCreated {gitattributes_path} with examples")
        print("  Edit this file to enable fid filter for your file patterns")
    else:
        print(f"\n{gitattributes_path} already exists")
        print("  Add filter=fid to patterns you want to manage with fid")
    
    print("\n✓ Git filter configured successfully!")
    print("\nNext steps:")
    print("  1. Edit .fidconfig with your server URL")
    print("  2. Edit .gitattributes to add file patterns")
    print("  3. Run: git add --renormalize .  (to apply filter to existing files)")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("--help", "-h", "help"):
        print("usage: fid_git.py <command>")
        print()
        print("commands:")
        print("  clean <filename>   - Clean filter (upload to server)")
        print("  smudge             - Smudge filter (download from server)")
        print("  init               - Initialize git filter for current repo")
        print()
        print("git setup:")
        print("  git config filter.fid.clean \"/path/to/fid_git.py clean\"")
        print("  git config filter.fid.smudge \"/path/to/fid_git.py smudge\"")
        print("  git config filter.fid.required true")
        sys.exit(0 if len(sys.argv) < 2 else 1)
    
    command = sys.argv[1]
    
    if command == "clean":
        # Git passes filename as second argument
        filename = sys.argv[2] if len(sys.argv) > 2 else None
        clean_filter(filename)
    
    elif command == "smudge":
        smudge_filter()
    
    elif command == "init":
        git_init()
    
    else:
        print(f"unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()