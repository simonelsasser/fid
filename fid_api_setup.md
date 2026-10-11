# Standalone fid API setup — macOS Monterey (12) and later

Use Apple's Apache and a system LaunchDaemon. Required: Python 3.9+, git,
administrator access, a hostname resolving to the server, and a TLS certificate
valid for that hostname. No Python packages are required.

This recipe deploys `fid_api.py` behind a reverse proxy mounted at `/api`.
The proxy strips `/api`; the backend uses token authentication.

Replace `example.domain.com`, `YOUR_USER`, `PYTHON_PATH`, certificate paths,
and `REPLACE_WITH_SECRET` before applying examples. Keep the same secret on
server and clients. Do not overwrite another service's `/api` route.

## 1. Install

```bash
xcode-select --install                  # only if Command Line Tools are absent
python3 --version                      # require 3.9 or newer
command -v python3                     # record absolute path as PYTHON_PATH
sudo mkdir -p /usr/local/bin
mkdir -p ~/GitHub
git clone https://github.com/simonelsasser/fid.git ~/GitHub/fid
cd ~/GitHub/fid
./fid_install.sh
fid --version
fid_api.py --help
```

For an existing checkout, use that checkout and rerun the installer. It copies
four scripts into `/usr/local/bin` (including `fid.py`, copied from a symlink).
On Monterey, Python may require Command Line Tools installation; if `python3`
is unavailable, install a Python distribution supporting Monterey. Use its
absolute executable path in the plist, rather than relying on launchd's PATH.

The default macOS data location remains `/Library/Application Support/fid`
(Linux: `~/.fid`). Set `FID_HOME` to override it for both CLI and API; `~` is
expanded and relative paths are resolved against the current working directory.
Use an absolute path for services. For a user-owned registry, run as that user:

```bash
export FID_HOME="$HOME/.fid"
mkdir -p "$FID_HOME"
```

Changing `FID_HOME` selects a different registry; it does not migrate existing
data. Set the same value in every CLI shell and in the service configuration.
Alternatively, to keep the system-wide default:
Choose the ordinary account that will run both the daemon and local fid CLI as
`YOUR_USER`. On a **new installation**, prepare writable data directories:

```bash
sudo mkdir -p "/Library/Application Support/fid/cache" "/Library/Application Support/fid/server_uploads"
sudo chown -R YOUR_USER:staff "/Library/Application Support/fid"
sudo chmod 700 "/Library/Application Support/fid"
```

On an existing installation, preserve its database and ownership; select its
owner as `YOUR_USER`. The daemon needs read/write access to the database, cache
and uploads, and read access to any registered files it should serve.

## 2. Choose authentication and test the backend

Generate once with `openssl rand -hex 32`, save the result, and use it wherever
`REPLACE_WITH_SECRET` appears. In the current shell:

```bash
KEY='REPLACE_WITH_SECRET'
PYTHON_PATH /usr/local/bin/fid_api.py --port 8080 --auth-key "$KEY"
```

Run as `YOUR_USER`. From another terminal, an authenticated
`curl -f -I -H "Authorization: Bearer $KEY" 'http://127.0.0.1:8080/download?fid=ID'`
must return 200 for a registered, available file (replace ID). Set `KEY` there
too. Stop the foreground server with Ctrl-C before step 4. The default exposes
download, resolve and verify. Add `--enable-upload` to enable `/upload` and `/list`;
uploads then default to the data directory's `server_uploads/`. The old
`--no-upload` flag has been replaced.
Uploads have a default 1 GiB limit. Override it with a positive byte count,
e.g. `--max-upload-bytes 536870912` for 512 MiB. Filenames must be plain
basenames, not paths; uploads cannot overwrite existing files or follow symlinks.

The API binds **all IPv4 interfaces**, with no `--host` option. Restrict inbound
TCP 8080 using the site's persistent host/network firewall while allowing local
Apache access; verify that other computers cannot reach port 8080 directly.
The auth key is still required.

## 3. Reverse proxy (required)

In `/etc/apache2/httpd.conf`, ensure these existing module lines are enabled
once, and `Listen 443` exists once:

```apache
LoadModule proxy_module libexec/apache2/mod_proxy.so
LoadModule proxy_http_module libexec/apache2/mod_proxy_http.so
LoadModule ssl_module libexec/apache2/mod_ssl.so
```

For a new hostname, create `/etc/apache2/extra/fid-https.conf`:

```apache
<VirtualHost *:443>
    ServerName example.domain.com
    SSLEngine on
    SSLCertificateFile /path/to/fullchain.pem
    SSLCertificateKeyFile /path/to/privkey.pem
    ProxyRequests Off
    ProxyPassInherit Off
    LimitRequestBody 0
    ProxyPass        /api/ http://127.0.0.1:8080/ timeout=300
    ProxyPassReverse /api/ http://127.0.0.1:8080/
</VirtualHost>
```

Add `Include /private/etc/apache2/extra/fid-https.conf` to `httpd.conf`.
For an existing hostname, put these proxy/body directives inside its existing
TLS vhost instead; place `/api/` rules **before** any catch-all `ProxyPass /`.
`ProxyPassInherit Off` excludes inherited global proxy rules; check that this
is appropriate for the chosen vhost. Both trailing slashes are intentional:
public `/api/resolve` becomes backend `/resolve`, and `/api/?fid=ID` becomes `/?fid=ID`.
The backend also accepts `/api/` aliases for all these routes, so proxies that
preserve the prefix work too. HEAD supports root/download routes only. The
same authentication and upload restrictions apply to either route form.

```bash
sudo /usr/sbin/apachectl configtest
sudo /usr/sbin/apachectl graceful       # reload an already-running Apache
```

If Apache is not running, enable/start its system daemon:
`sudo launchctl load -w /System/Library/LaunchDaemons/org.apache.httpd.plist`.
This legacy command remains available on Monterey. Check `sudo apachectl -S`
for the intended vhost. Open inbound 443 and arrange DNS for the chosen name.

## 4. Daemon: boot startup and automatic restart

Create `/Library/LaunchDaemons/com.fid.server.plist` with substituted values:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>Label</key><string>com.fid.server</string>
    <key>UserName</key><string>YOUR_USER</string>
    <key>EnvironmentVariables</key><dict>
        <key>FID_HOME</key><string>/Users/YOUR_USER/.fid</string>
    </dict>
    <key>ProgramArguments</key><array>
        <string>PYTHON_PATH</string>
        <string>/usr/local/bin/fid_api.py</string>
        <string>--port</string><string>8080</string>
        <string>--auth-key</string><string>REPLACE_WITH_SECRET</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>10</integer>
    <key>StandardOutPath</key><string>/var/log/fid_server.log</string>
    <key>StandardErrorPath</key><string>/var/log/fid_server_error.log</string>
</dict></plist>
```

For upload support, add `<string>--enable-upload</string>` inside the
`ProgramArguments` array. Omit it for download/resolve/verify only.
The example uses a user-owned registry: create it as `YOUR_USER`, and export
the same `FID_HOME` for local CLI commands. To use an existing system-wide
registry instead, set the plist's `FID_HOME` to `/Library/Application Support/fid`.

```bash
sudo chown root:wheel /Library/LaunchDaemons/com.fid.server.plist
sudo chmod 600 /Library/LaunchDaemons/com.fid.server.plist
sudo plutil -lint /Library/LaunchDaemons/com.fid.server.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/com.fid.server.plist
sudo launchctl print system/com.fid.server
```

These launchctl commands work on Monterey and later. `RunAtLoad` starts at boot;
`KeepAlive` relaunches on exit, including crashes. It does not detect a hung
process. Restart now: `sudo launchctl kickstart -k system/com.fid.server`.
After editing the plist, use `sudo launchctl bootout system/com.fid.server`,
then the bootstrap command again. Logs: `/var/log/fid_server_error.log`.
For unattended availability, disable system sleep while on power; FileVault
may require unlocking the Mac after reboot before services can start.

## 5. Verify through the proxy

Set `KEY` to the saved secret and choose a small, nonempty test file:

```bash
BASE='https://example.domain.com/api'
FILE='/absolute/path/to/test.txt'
FID=$(fid id --register "$FILE")       # full 22-character ID, no fid:// prefix
curl -f -H "Authorization: Bearer $KEY" "http://127.0.0.1:8080/resolve?fid=$FID"
curl -f -H "Authorization: Bearer $KEY" "$BASE/resolve?fid=$FID"
curl -f -H "Authorization: Bearer $KEY" "$BASE/verify?fid=$FID"
curl -f -I -H "Authorization: Bearer $KEY" "$BASE/?fid=$FID"
curl -f -H "Authorization: Bearer $KEY" "$BASE/?fid=$FID" -o /tmp/fid-api-download.txt
cmp "$FILE" /tmp/fid-api-download.txt
```

Expect `text/plain` filename bodies, HEAD 200, and identical bytes. Without
authentication, `/api/resolve?fid=ID` must return 401. With authentication,
`/api/list` and POST `/api/upload` return 404 by default. If `--enable-upload`
is set, test:

```bash
curl -f -H "Authorization: Bearer $KEY" --data-binary @"$FILE" "$BASE/upload?fid=$FID&filename=test.txt"
```

Expect upload JSON with `uploaded` or `existing`. Downloads stream in 1 MiB
chunks; uploads are held in RAM and requests handled serially. `/resolve` checks
existence and stored size when known, not MD5; same-size edits are not detected.
On success it returns the filename as plain text; on failure the body is empty.
`/verify?fid=ID` recalculates the MD5-based fid and returns the same filename
body on a match, or HTTP 409 (`FID_MISMATCH`) on changed content. It reads the
whole file and does not change the registered fid. Both accept full IDs or
unique prefixes.

Use a publicly trusted certificate or a private CA trusted by **the selected
Python interpreter**. For a private CA, `curl --cacert /path/to/ca.pem` tests it;
`SSL_CERT_FILE=/path/to/ca-bundle.pem fid resolve ...` supplies Python's CA bundle.
The certificate must also match the hostname. Keychain trust alone may not
suffice for every Python distribution.

Check the returned filename after deployment — it must match the basename of
the uploaded/registered file, never a path or URL:

```bash
RESOLVED_NAME=$(curl -f -H "Authorization: Bearer $KEY" "$BASE/resolve?fid=$FID")
echo "$RESOLVED_NAME"                  # e.g. test.txt
[ "$RESOLVED_NAME" = "$(basename "$FILE")" ] && echo "FILENAME OK"
curl -f -H "Authorization: Bearer $KEY" "$BASE/resolve?fid=$FID" -o /dev/null -w "status=%{http_code}\n"
```

Consult the server log (`/var/log/fid_server_error.log`) for details on a
non-200 response; response bodies are deliberately generic.

API responses never reveal internal paths: downloads identify a file only by
its basename (`Content-Disposition`), and error responses are generic — OS
error text that may embed a full path goes to the server log only.

### Troubleshooting database and macOS volume permissions

- **Read-only database:** the service user needs write access to the database
  **and its parent directory** (SQLite creates journal files there). Check
  `FID_HOME`, ownership and whether the volume is mounted read-only. Do not
  recursively change ownership of an existing shared registry without checking
  which accounts use it.
- **Operation not permitted:** filesystem ownership and macOS privacy/TCC are
  separate. Grant access to the app actually reading the files: Terminal/iTerm
  for interactive use, or the Python executable/service app for launchd.
  Monterey: System Preferences → Security & Privacy → Privacy; newer macOS:
  System Settings → Privacy & Security. Check Files and Folders (including
  removable/network volumes), or Full Disk Access when needed.
- A terminal's privacy grant does not automatically cover a LaunchDaemon.
  Restart the terminal/service after changing grants. Run the daemon under the
  intended data-owning user and test a real file on the external volume.
  `sudo` does not bypass TCC; launching an app with `open` is not a general fix.
- The volume must be mounted before use; check its actual path under `/Volumes`.
  Client data directories must be writable too. API errors stay generic;
  detailed permission diagnostics appear in the server's stderr log.

## 6. Other machines (optional)

Install fid using step 1 and prepare the writable macOS data directory for
the client account. Register the HTTPS base URL **with a trailing slash**:

```bash
fid remote add https://example.domain.com/api/ "$KEY"
fid remote
fid resolve "fid://$FID"
fid verify "fid://$FID"
```

Use the full ID from step 5 and the same secret. Successful resolution downloads,
checks MD5 and caches the file. `fid remote rm https://example.domain.com/api/`
removes the remote. Prefer registration for this authenticated HTTPS setup:
bare `@hostname` defaults to HTTP and does not supply a secret itself.
