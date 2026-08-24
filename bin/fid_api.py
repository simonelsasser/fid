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
                
                self.send_success_response({"fid": md5_b62, "path": path})
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
                
                self.send_success_response({"fid": md5_b62, "path": upload_path})
                return
        
        self.send_error_response(404, "fid not found")
    
    def handle_upload(self):
        """Handle /upload POST request.
        
        Flow:
        1. Save uploaded file with original filename (temporarily)
        2. Register with --no-duplicate flag
        3. If ERROR:DUPLICATE_FILE: delete temp file, return duplicate status
        4. If success: create <fid>/ directory, move file there, update database
        """
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
        
        # Use timestamp for temp filename to avoid collisions
        import time
        temp_filename = f".upload_{int(time.time() * 1000)}_{filename or 'tmp'}"
        temp_path = os.path.join(self.config.upload_dir, temp_filename)
        
        # Read uploaded content
        try:
            content = self.rfile.read(content_length)
        except Exception as e:
            self.send_error_response(500, f"cannot read upload: {e}")
            return
        
        # Save uploaded file temporarily
        try:
            with open(temp_path, "wb") as f:
                f.write(content)
        except Exception as e:
            self.send_error_response(500, f"cannot save file: {e}")
            return
        
        # Register with --no-duplicate flag to check for existing files
        try:
            fid = register_single(temp_path, no_duplicate=True)
            
            if not fid:
                # Clean up temp file
                try:
                    os.remove(temp_path)
                except:
                    pass
                self.send_error_response(500, "failed to register file")
                return
        except Exception as e:
            import traceback
            traceback.print_exc()
            # Clean up temp file
            try:
                os.remove(temp_path)
            except:
                pass
            self.send_error_response(500, f"failed to register: {e}")
            return
        
        # Check if duplicate was detected (stderr would have ERROR:DUPLICATE_FILE)
        # We need to capture stderr from register_single, but since we can't,
        # we check if the fid directory already exists
        fid_dir = os.path.join(self.config.upload_dir, fid)
        
        if os.path.isdir(fid_dir):
            # Duplicate - file already stored in <fid>/ directory
            try:
                os.remove(temp_path)
            except:
                pass
            
            self.send_success_response({
                "fid": fid,
                "status": "duplicate",
                "message": "file already exists on server"
            })
            return
        
        # Not a duplicate - create <fid>/ directory and move file there
        try:
            os.makedirs(fid_dir, exist_ok=True)
            
            # Use original filename if available, otherwise use fid
            final_filename = filename if filename else fid
            final_path = os.path.join(fid_dir, final_filename)
            
            os.rename(temp_path, final_path)
            
            # Update database with new path using fid update
            from fid import update_single
            update_single(fid, temp_path, final_path)
            
            self.send_success_response({
                "fid": fid,
                "status": "uploaded"
            })
        except Exception as e:
            # Clean up temp file if move failed
            try:
                os.remove(temp_path)
            except:
                pass
            self.send_error_response(500, f"cannot finalize upload: {e}")
    
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