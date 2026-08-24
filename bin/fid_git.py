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

# Script directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Add to path to import fid module
sys.path.insert(0, SCRIPT_DIR)


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


def upload_to_server(content, filename, config, auth_key):
    """Upload content to fid server and return fid."""
    import urllib.request
    
    server_url = get_server_url(config)
    if not server_url:
        print("fid-git: error: no server configured", file=sys.stderr)
        sys.exit(1)
    
    # Ensure server URL doesn't have trailing slash
    server_url = server_url.rstrip("/")
    
    # Upload to server
    upload_url = f"{server_url}/upload"
    
    try:
        req = urllib.request.Request(
            upload_url,
            data=content,
            method="POST"
        )
        req.add_header("Content-Type", "application/octet-stream")
        
        # Add filename to Content-Disposition
        if filename:
            req.add_header("Content-Disposition", f'attachment; filename="{filename}"')
        
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


def clean_filter(filename):
    """
    Clean filter: read file from stdin, upload to server, write fid pointer to stdout.
    
    Git calls this when adding files to the index.
    """
    # Read file content from stdin
    content = sys.stdin.buffer.read()
    
    # Get repo root and config
    repo_root = get_repo_root()
    if not repo_root:
        # Not in a git repo, pass through
        sys.stdout.buffer.write(content)
        return
    
    config = find_config(repo_root)
    if not config:
        # No .fidconfig, pass through
        sys.stdout.buffer.write(content)
        return
    
    auth_key = get_auth_key(config, repo_root)
    
    # Upload to server
    fid = upload_to_server(content, filename, config, auth_key)
    
    # Write fid pointer to stdout
    pointer = f"fid://{fid}\n".encode()
    sys.stdout.buffer.write(pointer)


def smudge_filter():
    """
    Smudge filter: read fid pointer from stdin, download from server, write content to stdout.
    
    Git calls this when checking out files from the index.
    """
    # Read fid pointer from stdin
    content = sys.stdin.read().strip()
    
    # Check if it's a valid fid pointer
    if not validate_fid(content):
        # Not a fid pointer, pass through as-is
        sys.stdout.write(content + "\n")
        return
    
    fid = content[6:]  # Remove "fid://" prefix
    
    # Get repo root and config
    repo_root = get_repo_root()
    if not repo_root:
        # Not in a git repo, can't download
        print(f"fid-git: warning: not in git repo, cannot download {fid}", file=sys.stderr)
        sys.stdout.write(content + "\n")
        return
    
    config = find_config(repo_root)
    if not config:
        # No .fidconfig, can't download
        print(f"fid-git: warning: no .fidconfig, cannot download {fid}", file=sys.stderr)
        sys.stdout.write(content + "\n")
        return
    
    auth_key = get_auth_key(config, repo_root)
    
    # Download from server
    downloaded = download_from_server(fid, config, auth_key)
    
    if downloaded is None:
        # Download failed, output fid pointer as-is
        print(f"fid-git: warning: could not download {fid}, file will be missing", file=sys.stderr)
        sys.stdout.write(content + "\n")
        return
    
    # Write content to stdout
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