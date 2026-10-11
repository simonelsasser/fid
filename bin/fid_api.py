#!/usr/bin/env python3
"""
Fid Server - HTTP API server for fid file management

Provides download, resolve and verify endpoints; uploads and listing are opt-in.
"""

import os
import sys
import argparse
import hashlib
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import shutil
import sqlite3
from functools import wraps

# Import fid functions from the same directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from fid import (
    db,
    lookup,
    base62_decode,
    normalize_path,
    split_path,
    md5_file,
    register_single,
    base62_encode,
    FID_HOME,
    CACHE_DIR,
    permission_error_message,
)


################ SERVER CONFIG ################

DEFAULT_PORT = 8080
DEFAULT_UPLOAD_DIR = os.path.join(FID_HOME, "server_uploads")
DEFAULT_MAX_UPLOAD_BYTES = 1024 * 1024 * 1024


def storage_errors(handler):
    """Convert storage failures to generic API errors, with local diagnostics."""
    @wraps(handler)
    def wrapped(self, *args, **kwargs):
        try:
            return handler(self, *args, **kwargs)
        except (OSError, sqlite3.Error) as error:
            message = permission_error_message(error)
            print(f"fid-api: {message or error}", file=sys.stderr)
            status = 403 if isinstance(error, PermissionError) else 500
            if self.command == "HEAD":
                self.send_response(status)
                self.end_headers()
            else:
                self.send_error_response(status, "storage access denied" if message else "storage error")
    return wrapped


class FidServerConfig:
    """Server configuration."""
    
    def __init__(self, port, upload_dir, auth_key=None, allow_upload=False,
                 max_upload_bytes=DEFAULT_MAX_UPLOAD_BYTES):
        self.port = port
        self.upload_dir = upload_dir
        self.auth_key = auth_key
        self.allow_upload = allow_upload
        if max_upload_bytes <= 0:
            raise ValueError("max_upload_bytes must be positive")
        self.max_upload_bytes = max_upload_bytes
        self.upload_dir = os.path.abspath(os.path.expanduser(upload_dir))
        
        # Ensure upload directory exists
        if self.allow_upload:
            os.makedirs(self.upload_dir, exist_ok=True)


################ REQUEST HANDLER ################

class FidRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for fid API."""
    
    config = None  # Will be set by server
    
    def log_message(self, format, *args):
        """Override to use stderr."""
        sys.stderr.write(f"[fid-server] {args[0]}\n")
    
    def send_error_response(self, code, message):
        """Send JSON error response."""
        response = json.dumps({"error": message}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(response))
        self.end_headers()
        self.wfile.write(response)
    
    def send_empty_response(self, code, content_type="text/plain"):
        """Send a response with an empty body and no content headers."""
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", 0)
        self.end_headers()
    
    def send_success_response(self, data, content_type="application/json"):
        """Send a 200 response.

        JSON data is serialized; any other content_type takes bytes, or str
        which is UTF-8 encoded. The Content-Type header is always sent.
        """
        self.send_response(200)
        if content_type == "application/json":
            response = json.dumps(data).encode()
        elif isinstance(data, str):
            response = data.encode("utf-8")
        else:
            response = data
        
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(response))
        self.end_headers()
        self.wfile.write(response)
    
    def send_file(self, filepath):
        """Send file as download."""
        if not os.path.exists(filepath):
            self.send_error_response(404, "file not found")
            return
        
        headers_sent = False
        try:
            with open(filepath, "rb") as f:
                size = os.fstat(f.fileno()).st_size
                basename = os.path.basename(filepath)
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{basename}"')
                self.send_header("Content-Length", size)
                self.end_headers()
                headers_sent = True
                shutil.copyfileobj(f, self.wfile, length=1024 * 1024)
        except (BrokenPipeError, ConnectionResetError):
            # A disconnected download client needs no further response.
            return
        except Exception as e:
            if headers_sent:
                self.close_connection = True
                print(f"fid-api: download failed: {e}", file=sys.stderr)
            else:
                message = permission_error_message(e)
                if message:
                    print(f"fid-api: {message}", file=sys.stderr)
                self.send_error_response(403 if isinstance(e, PermissionError) else 500, "cannot read file")
    
    def check_auth(self):
        """Check authentication if configured."""
        if not self.config.auth_key:
            return True
        
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            return token == self.config.auth_key
        
        # Also check query parameter
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if params.get("token", [None])[0] == self.config.auth_key:
            return True
        
        return False
    
    @storage_errors
    def do_GET(self):
        """Handle GET requests."""
        if not self.check_auth():
            self.send_error_response(401, "unauthorized")
            return
        
        parsed = urlparse(self.path)
        path = self.api_path(parsed.path)
        params = parse_qs(parsed.query)
        
        if path in ("/download", "/api/download"):
            self.handle_download(params)
        elif path == "/resolve":
            self.handle_resolve(params)
        elif path == "/verify":
            self.handle_resolve(params, verify=True)
        elif path == "/list" and self.config.allow_upload:
            self.handle_list()
        elif path == "/" and "fid" in params:
            # Backward compatibility: support ?fid=XXX at root for fid remote
            self.handle_download(params)
        else:
            self.send_error_response(404, "not found")
    
    @staticmethod
    def api_path(path):
        """Accept the same routes with or without exactly one /api prefix."""
        if path == "/api":
            return "/"
        return path[4:] if path.startswith("/api/") else path

    @storage_errors
    def do_HEAD(self):
        """Handle HEAD requests (for remote file existence check)."""
        if not self.check_auth():
            self.send_response(401)
            self.end_headers()
            return
        
        parsed = urlparse(self.path)
        path = self.api_path(parsed.path)
        params = parse_qs(parsed.query)
        
        # Support root and both download endpoint forms.
        if path in ("/", "/download", "/api/download"):
            if "fid" in params:
                self.handle_head_download(params)
                return
        
        self.send_response(404)
        self.end_headers()
    
    @storage_errors
    def do_POST(self):
        """Handle POST requests."""
        if not self.check_auth():
            self.send_error_response(401, "unauthorized")
            return
        
        parsed = urlparse(self.path)
        path = self.api_path(parsed.path)
        
        if path == "/upload":
            if not self.config.allow_upload:
                self.send_error_response(404, "not found")
                return
            self.handle_upload()
        else:
            self.send_error_response(404, "not found")
    
    def handle_download(self, params):
        """Handle /download?fid=<fid> request."""
        fid_list = params.get("fid", [])
        
        if not fid_list:
            self.send_error_response(400, "missing fid parameter")
            return
        
        fid_prefix = fid_list[0]
        
        # Look up fid in database
        conn = db()
        matches = lookup(conn, fid_prefix)
        
        if len(matches) != 1:
            self.send_error_response(404, "fid not found")
            return
        
        md5_hex, md5_b62 = matches[0]
        
        # Find local path
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT path FROM locations WHERE md5_hex=?",
            (md5_hex,)
        ).fetchall()
        
        for (path,) in rows:
            pfx, val = split_path(path)
            if pfx == "local" and os.path.exists(val):
                # Verify file size matches stored size
                try:
                    stored_size = cur.execute(
                        "SELECT size FROM files WHERE md5_hex=?",
                        (md5_hex,)
                    ).fetchone()
                    if stored_size and stored_size[0] is not None:
                        current_size = os.path.getsize(val)
                        if current_size != stored_size[0]:
                            print(f"fid-api: size mismatch for {val}", file=sys.stderr)
                            continue  # Try next path
                except Exception as e:
                    print(f"fid-api: cannot verify size: {e}", file=sys.stderr)
                
                self.send_file(val)
                return
        
        # Check server uploads directory (<fid>/<filename> structure)
        fid_dir = os.path.join(self.config.upload_dir, md5_b62)
        if os.path.isdir(fid_dir):
            # Get first file in directory (should be only one)
            files = os.listdir(fid_dir)
            if files:
                upload_path = os.path.join(fid_dir, files[0])
                # Verify file size matches stored size
                try:
                    stored_size = cur.execute(
                        "SELECT size FROM files WHERE md5_hex=?",
                        (md5_hex,)
                    ).fetchone()
                    if stored_size and stored_size[0] is not None:
                        current_size = os.path.getsize(upload_path)
                        if current_size != stored_size[0]:
                            print(f"fid-api: size mismatch for {upload_path}", file=sys.stderr)
                            self.send_error_response(500, "file corrupted")
                            return
                except Exception as e:
                    print(f"fid-api: cannot verify size: {e}", file=sys.stderr)
                
                self.send_file(upload_path)
                return
        
        self.send_error_response(404, "file not available")
    
    def handle_head_download(self, params):
        """Handle HEAD request for /download?fid=<fid> - check file exists."""
        fid_list = params.get("fid", [])
        
        if not fid_list:
            self.send_response(400)
            self.end_headers()
            return
        
        fid_prefix = fid_list[0]
        
        # Look up fid in database
        conn = db()
        matches = lookup(conn, fid_prefix)
        
        if len(matches) != 1:
            self.send_response(404)
            self.end_headers()
            return
        
        md5_hex, md5_b62 = matches[0]
        
        # Find local path
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT path FROM locations WHERE md5_hex=?",
            (md5_hex,)
        ).fetchall()
        
        for (path,) in rows:
            pfx, val = split_path(path)
            if pfx == "local" and os.path.exists(val):
                self.send_response(200)
                basename = os.path.basename(val)
                self.send_header("Content-Disposition", f'attachment; filename="{basename}"')
                self.end_headers()
                return
        
        # Check server uploads directory (<fid>/<filename> structure)
        fid_dir = os.path.join(self.config.upload_dir, md5_b62)
        if os.path.isdir(fid_dir):
            # Get first file in directory (should be only one)
            files = os.listdir(fid_dir)
            if files:
                upload_path = os.path.join(fid_dir, files[0])
                self.send_response(200)
                basename = os.path.basename(upload_path)
                self.send_header("Content-Disposition", f'attachment; filename="{basename}"')
                self.end_headers()
                return
        
        self.send_response(404)
        self.end_headers()
    
    def handle_resolve(self, params, verify=False):
        """Handle /resolve or /verify?fid=<fid> requests.

        Uses fid.resolve_single() for path resolution with self-healing.
        Returns the plain filename of the resolved file (never an internal
        path or a download URL). Verification recalculates the content hash,
        without changing the fid; a mismatch returns 409.
        """
        fid_list = params.get("fid", [])
        
        if not fid_list:
            self.send_error_response(400, "missing fid parameter")
            return
        
        fid_prefix = fid_list[0]
        
        # Use fid.resolve_single() which handles:
        # - Prefix lookup
        # - Path existence checking
        # - Self-healing (removes stale paths)
        # - Size validation (if check_size=True)
        from fid import resolve_single
        
        # Verify hashes even size-mismatched files to report a content mismatch.
        resolved_path = resolve_single(fid_prefix, check_size=not verify)
        
        if resolved_path is None:
            # resolve_single() already printed error and cleaned up stale paths
            self.send_empty_response(404)
            return
        
        # Get the full fid (resolve_single validates it exists)
        conn = db()
        matches = lookup(conn, fid_prefix)
        md5_hex, md5_b62 = matches[0]

        if verify:
            try:
                actual_fid = base62_encode(md5_file(resolved_path))
            except OSError as error:
                print(f"fid-api: {permission_error_message(error) or error}", file=sys.stderr)
                self.send_error_response(403 if isinstance(error, PermissionError) else 500, "cannot read file for verification")
                return
            if actual_fid != md5_b62:
                self.send_error_response(409, "FID_MISMATCH")
                return
        
        # Only expose the filename; never the internal path.
        filename = os.path.basename(resolved_path.rstrip("/")) or fid_prefix
        self.send_success_response(
            filename.encode("utf-8"),
            content_type="text/plain; charset=utf-8",
        )
    
    def handle_upload(self):
        """Handle /upload POST request with fid and filename from query parameters.
        
        Flow:
        1. Get fid and filename from query parameters
        2. Read content and calculate fid
        3. Verify calculated fid matches provided fid (security check)
        4. Check if fid already exists on server (duplicate detection)
        5. If duplicate: delete content, return duplicate status
        6. If new: save as <fid>/<filename>, register in database
        """
        # Parse query parameters
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        
        # Get fid from query parameter
        fid_list = params.get("fid", [])
        if not fid_list:
            self.send_error_response(400, "missing fid parameter")
            return
        provided_fid = fid_list[0]
        
        # Validate fid format
        if len(provided_fid) != 22 or any(c not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz" for c in provided_fid):
            self.send_error_response(400, "invalid fid format")
            return
        
        # Get filename from query parameter
        filename_list = params.get("filename", [])
        filename = filename_list[0] if filename_list else None
        if filename is not None and (
            filename in (".", "..") or "/" in filename or "\\" in filename
            or ":" in filename or any(ord(c) < 32 or ord(c) == 127 for c in filename)
        ):
            self.send_error_response(400, "invalid filename")
            self.close_connection = True
            return
        
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            self.send_error_response(400, "invalid or missing Content-Length")
            self.close_connection = True
            return
        content_length = int(lengths[0])
        if content_length > self.config.max_upload_bytes:
            self.send_error_response(413, "upload exceeds size limit")
            self.close_connection = True
            return
        
        if content_length == 0:
            self.send_error_response(400, "no file content")
            return
        
        # Read uploaded content
        try:
            content = self.rfile.read(content_length)
        except Exception as e:
            print(f"fid-api: cannot read upload: {e}", file=sys.stderr)
            self.send_error_response(500, "cannot read upload")
            return
        
        # Calculate fid from content and verify it matches provided fid
        if len(content) != content_length:
            self.send_error_response(400, "incomplete upload")
            self.close_connection = True
            return
        import hashlib
        md5_hash = hashlib.md5(content)
        md5_bytes = md5_hash.digest()
        md5_hex = md5_hash.hexdigest()
        from fid import base62_encode
        calculated_fid = base62_encode(md5_bytes)
        
        if calculated_fid != provided_fid:
            # Fid mismatch - reject upload (security check)
            print(f"fid-api: fid mismatch - provided={provided_fid}, calculated={calculated_fid}", file=sys.stderr)
            self.send_error_response(400, "FID_MISMATCH")
            return
        
        # Check if fid already exists in uploads directory (universal check using database + size)
        # This is the same check used by "fid resolve <fid> --check-size"
        # But we only check server upload paths, not local paths
        conn = db()
        cur = conn.cursor()
        
        # Get stored size for this fid
        stored = cur.execute(
            "SELECT size FROM files WHERE md5_hex=?",
            (md5_hex,)
        ).fetchone()
        
        if stored and stored[0] is not None and stored[0] == len(content):
            # Size matches - check if any server upload path exists
            upload_paths = cur.execute(
                "SELECT path FROM locations WHERE md5_hex=?",
                (md5_hex,)
            ).fetchall()
            
            for (path,) in upload_paths:
                pfx, val = split_path(path)
                # Check if path is under server uploads directory
                if os.path.commonpath((os.path.abspath(val), self.config.upload_dir)) == self.config.upload_dir and os.path.exists(val):
                    # File exists in server uploads with matching size
                    self.send_success_response({
                        "fid": provided_fid,
                        "status": "existing",
                        "message": "file already exists on server"
                    })
                    return
        
        # Save file to uploads directory in <fid>/<filename> structure
        created_file = False
        try:
            fid_dir = os.path.join(self.config.upload_dir, provided_fid)
            if os.path.islink(fid_dir):
                self.send_error_response(400, "unsafe upload path")
                return
            os.makedirs(fid_dir, exist_ok=True)
            
            # Use provided filename or fallback to fid
            final_filename = filename if filename else provided_fid
            final_path = os.path.join(fid_dir, final_filename)
            upload_root = os.path.realpath(self.config.upload_dir)
            if os.path.commonpath((upload_root, os.path.realpath(final_path))) != upload_root or os.path.islink(final_path):
                self.send_error_response(400, "unsafe upload path")
                return
            
            # Never follow a symlink or overwrite an existing file.
            with open(final_path, "xb") as f:
                created_file = True
                f.write(content)
            
            # Register in fid database
            register_single(final_path)
            
            self.send_success_response({
                "fid": provided_fid,
                "status": "uploaded"
            })
            
        except Exception as e:
            # Clean up on failure
            import traceback
            print(f"fid-api: cannot save upload: {e}", file=sys.stderr)
            traceback.print_exc()
            try:
                if created_file and os.path.exists(final_path):
                    os.remove(final_path)
                if os.path.isdir(fid_dir) and not os.listdir(fid_dir):
                    os.rmdir(fid_dir)
            except:
                pass
            message = permission_error_message(e)
            if message:
                print(f"fid-api: {message}", file=sys.stderr)
            if isinstance(e, FileExistsError):
                self.send_error_response(409, "upload file already exists")
            else:
                self.send_error_response(403 if isinstance(e, PermissionError) else 500, "cannot save file")
    
    def handle_list(self):
        """Handle /list request."""
        conn = db()
        cur = conn.cursor()
        
        rows = cur.execute(
            "SELECT md5_base62 FROM files ORDER BY md5_base62"
        ).fetchall()
        
        fids = [row[0] for row in rows]
        
        # Return as newline-separated list
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        content = "\n".join(fids)
        if fids:
            content += "\n"
        self.send_header("Content-Length", len(content.encode()))
        self.end_headers()
        self.wfile.write(content.encode())


################ SERVER ################

class FidServer:
    """Fid HTTP server."""
    
    def __init__(self, config):
        self.config = config
        self.server = None
    
    def start(self):
        """Start the server."""
        FidRequestHandler.config = self.config
        
        self.server = HTTPServer(("", self.config.port), FidRequestHandler)
        
        print(f"[fid-server] Starting on port {self.config.port}", file=sys.stderr)
        print(f"[fid-server] Upload directory: {self.config.upload_dir}", file=sys.stderr)
        print(f"[fid-server] Upload permitted: {self.config.allow_upload}", file=sys.stderr)
        if self.config.auth_key:
            print(f"[fid-server] Authentication: enabled", file=sys.stderr)
        else:
            print(f"[fid-server] Authentication: disabled", file=sys.stderr)
        
        try:
            self.server.serve_forever()
        except KeyboardInterrupt:
            print("\n[fid-server] Shutting down...", file=sys.stderr)
            self.stop()
    
    def stop(self):
        """Stop the server."""
        if self.server:
            self.server.shutdown()
            print("[fid-server] Server stopped", file=sys.stderr)


################ CLI ################

def main():
    parser = argparse.ArgumentParser(
        description="Fid HTTP API Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  fid-server --port 8080
  fid-server --port 8080 --auth-key mysecret
  fid-server --port 8080 --enable-upload
  fid-server --port 8080 --upload-dir /path/to/uploads
        """
    )
    
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to listen on (default: {DEFAULT_PORT})"
    )
    
    parser.add_argument(
        "--auth-key", "-k",
        type=str,
        default=None,
        help="Authentication key (Bearer token). If not set, no authentication required."
    )
    
    parser.add_argument(
        "--upload-dir", "-u",
        type=str,
        default=DEFAULT_UPLOAD_DIR,
        help=f"Directory to store uploaded files (default: {DEFAULT_UPLOAD_DIR})"
    )
    
    parser.add_argument(
        "--enable-upload",
        action="store_true",
        help="Enable upload and list endpoints (disabled by default)"
    )

    parser.add_argument(
        "--max-upload-bytes",
        type=int,
        default=DEFAULT_MAX_UPLOAD_BYTES,
        help="Maximum upload size in bytes (default: 1073741824, 1 GiB; must be positive)"
    )
    
    args = parser.parse_args()
    if args.max_upload_bytes <= 0:
        parser.error("--max-upload-bytes must be positive")
    
    config = FidServerConfig(
        port=args.port,
        upload_dir=args.upload_dir,
        auth_key=args.auth_key,
        allow_upload=args.enable_upload,
        max_upload_bytes=args.max_upload_bytes
    )
    
    server = FidServer(config)
    server.start()


if __name__ == "__main__":
    try:
        main()
    except (OSError, sqlite3.Error) as error:
        print(f"fid-api: {permission_error_message(error) or error}", file=sys.stderr)
        sys.exit(1)
