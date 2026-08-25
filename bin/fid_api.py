#!/usr/bin/env python3
"""
Fid Server - HTTP API server for fid file management

Provides REST API endpoints for downloading, uploading, and listing fids.
"""

import os
import sys
import argparse
import hashlib
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import shutil

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
)


################ SERVER CONFIG ################

DEFAULT_PORT = 8080
DEFAULT_UPLOAD_DIR = os.path.join(FID_HOME, "server_uploads")


class FidServerConfig:
    """Server configuration."""
    
    def __init__(self, port, upload_dir, auth_key=None, allow_upload=True):
        self.port = port
        self.upload_dir = upload_dir
        self.auth_key = auth_key
        self.allow_upload = allow_upload
        
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
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}).encode())
    
    def send_success_response(self, data, content_type="application/json"):
        """Send success response."""
        self.send_response(200)
        if content_type == "application/json":
            self.send_header("Content-Type", "application/json")
            response = json.dumps(data).encode()
        else:
            response = data
        
        self.send_header("Content-Length", len(response))
        self.end_headers()
        self.wfile.write(response)
    
    def send_file(self, filepath):
        """Send file as download."""
        if not os.path.exists(filepath):
            self.send_error_response(404, "file not found")
            return
        
        try:
            with open(filepath, "rb") as f:
                content = f.read()
            
            basename = os.path.basename(filepath)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{basename}"')
            self.send_header("Content-Length", len(content))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error_response(500, f"cannot read file: {e}")
    
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
    
    def do_GET(self):
        """Handle GET requests."""
        if not self.check_auth():
            self.send_error_response(401, "unauthorized")
            return
        
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        
        if path == "/download":
            self.handle_download(params)
        elif path == "/resolve":
            self.handle_resolve(params)
        elif path == "/list":
            self.handle_list()
        elif path == "/" and "fid" in params:
            # Backward compatibility: support ?fid=XXX at root for fid remote
            self.handle_download(params)
        else:
            self.send_error_response(404, "not found")
    
    def do_HEAD(self):
        """Handle HEAD requests (for remote file existence check)."""
        if not self.check_auth():
            self.send_response(401)
            self.end_headers()
            return
        
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        
        # Support both /?fid=XXX and /download?fid=XXX
        if path == "/" or path == "/download":
            if "fid" in params:
                self.handle_head_download(params)
                return
        
        self.send_response(404)
        self.end_headers()
    
    def do_POST(self):
        """Handle POST requests."""
        if not self.check_auth():
            self.send_error_response(401, "unauthorized")
            return
        
        parsed = urlparse(self.path)
        path = parsed.path
        
        if path == "/upload":
            if not self.config.allow_upload:
                self.send_error_response(403, "upload not permitted")
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
    
    def handle_resolve(self, params):
        """Handle /resolve?fid=<fid> request.
        
        Returns download URL instead of internal path for security.
        """
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
        
        # Construct download URL (never expose internal paths)
        # Get server URL from request
        host = self.headers.get("Host", "localhost")
        download_url = f"http://{host}/?fid={md5_b62}"
        
        # Find local path (just to verify file exists)
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
                
                # File exists, return download URL
                self.send_success_response({"fid": md5_b62, "url": download_url})
                return
        
        # Fid not found or no valid paths
        self.send_error_response(404, "fid not found")
    
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
        if len(provided_fid) != 22:
            self.send_error_response(400, "invalid fid format")
            return
        
        # Get filename from query parameter
        filename_list = params.get("filename", [])
        filename = filename_list[0] if filename_list else None
        
        content_length = int(self.headers.get("Content-Length", 0))
        
        if content_length == 0:
            self.send_error_response(400, "no file content")
            return
        
        # Read uploaded content
        try:
            content = self.rfile.read(content_length)
        except Exception as e:
            self.send_error_response(500, f"cannot read upload: {e}")
            return
        
        # Calculate fid from content and verify it matches provided fid
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
                if val.startswith(self.config.upload_dir) and os.path.exists(val):
                    # File exists in server uploads with matching size
                    self.send_success_response({
                        "fid": provided_fid,
                        "status": "existing",
                        "message": "file already exists on server"
                    })
                    return
        
        # Save file to uploads directory in <fid>/<filename> structure
        try:
            fid_dir = os.path.join(self.config.upload_dir, provided_fid)
            os.makedirs(fid_dir, exist_ok=True)
            
            # Use provided filename or fallback to fid
            final_filename = filename if filename else provided_fid
            final_path = os.path.join(fid_dir, final_filename)
            
            # Write file
            with open(final_path, "wb") as f:
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
            traceback.print_exc()
            try:
                if os.path.exists(final_path):
                    os.remove(final_path)
                if os.path.isdir(fid_dir) and not os.listdir(fid_dir):
                    os.rmdir(fid_dir)
            except:
                pass
            self.send_error_response(500, f"cannot save file: {e}")
    
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
  fid-server --port 8080 --no-upload
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
        "--no-upload",
        action="store_true",
        help="Disable upload endpoint"
    )
    
    args = parser.parse_args()
    
    config = FidServerConfig(
        port=args.port,
        upload_dir=args.upload_dir,
        auth_key=args.auth_key,
        allow_upload=not args.no_upload
    )
    
    server = FidServer(config)
    server.start()


if __name__ == "__main__":
    main()