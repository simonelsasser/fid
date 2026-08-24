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

from fid_cli import (
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
        else:
            self.send_error_response(404, "not found")
    
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
                self.send_file(val)
                return
        
        # Check server uploads directory
        upload_path = os.path.join(self.config.upload_dir, md5_b62)
        if os.path.exists(upload_path):
            self.send_file(upload_path)
            return
        
        self.send_error_response(404, "file not available")
    
    def handle_resolve(self, params):
        """Handle /resolve?fid=<fid> request."""
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
                self.send_success_response({"fid": md5_b62, "path": path})
                return
        
        # Check server uploads directory
        upload_path = os.path.join(self.config.upload_dir, md5_b62)
        if os.path.exists(upload_path):
            self.send_success_response({"fid": md5_b62, "path": upload_path})
            return
        
        self.send_error_response(404, "fid not found")
    
    def handle_upload(self):
        """Handle /upload POST request."""
        content_length = int(self.headers.get("Content-Length", 0))
        
        if content_length == 0:
            self.send_error_response(400, "no file content")
            return
        
        # Get filename from Content-Disposition or use fid
        content_disp = self.headers.get("Content-Disposition", "")
        filename = None
        if "filename=" in content_disp:
            import re
            m = re.search(r'filename="([^"]+)"', content_disp)
            if m:
                filename = m.group(1)
        
        # Read uploaded content
        try:
            content = self.rfile.read(content_length)
        except Exception as e:
            self.send_error_response(500, f"cannot read upload: {e}")
            return
        
        # Calculate MD5
        md5 = hashlib.md5(content)
        md5_bytes = md5.digest()
        md5_hex = md5.hex()
        
        md5_b62 = base62_encode(md5_bytes)
        
        # Check if file already exists
        upload_path = os.path.join(self.config.upload_dir, md5_b62)
        
        if os.path.exists(upload_path):
            # File already exists, delete uploaded content
            self.send_success_response({
                "fid": md5_b62,
                "status": "duplicate",
                "message": "file already exists on server"
            })
            return
        
        # Save uploaded file
        try:
            with open(upload_path, "wb") as f:
                f.write(content)
            
            # Register in fid database
            fid = register_single(upload_path)
            
            if fid:
                self.send_success_response({
                    "fid": fid,
                    "status": "uploaded"
                })
            else:
                self.send_error_response(500, "failed to register file")
        except Exception as e:
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