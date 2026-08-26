# fid — Design Specification v1.0

`fid` is a **content-addressable file identity and location registry CLI**.

It assigns immutable IDs to files based on their MD5 checksum, stores multiple storage locations per file, supports metadata tagging, and resolves files transparently across local storage, cache, and remote sources.

Primary goal:

> Always refer to primary data files by stable identity (`fid://ID`) instead of filesystem paths.

Typical use cases:

* sequencing raw files
* instrument output
* microscopy data
* large binary research datasets
* reproducible workflows
* distributed storage environments

---

# Identity Model

Each file receives:

```
fid://<22-char-base62-md5>
```

Example:

```
fid://3D7p8Kf9Ls0ZxW2jQnRtYu
```

Example prefix lookup:

```
fid resolve fid://3D7p8Kf9Ls0ZxW2j
```

Allowed if unique.

---

# Storage Model

SQLite database:

```
~/.fid/fid.db
```

Cache directory:

```
~/.fid/cache/
```

Tables:

## files

```
md5_hex
md5_base62
size
created
```

## locations

```
md5_hex
path
added
```

Multiple entries allowed per file.

## metadata

```
md5_hex
key
value
```

Unlimited flexible attributes.

---

# Location Model

Each file may exist at multiple locations.

Supported schemes:

| scheme | example                                    |
| ------ | ------------------------------------------ |
| local  | /data/sample.fastq                         |
| http   | [http://server/file](http://server/file)   |
| https  | [https://server/file](https://server/file) |
| ftp    | ftp://server/file                          |
| sftp   | sftp://user@host/file                      |

Implicit default:

```
local filesystem path
```

Example:

```
fid register file.fastq
```

equals:

```
fid register /absolute/path/file.fastq
```

---

# Resolve Strategy

When resolving:

```
fid resolve fid://ABC123
```

Order:

### 1

existing local filesystem copies

### 2

cached copies

```
~/.fid/cache/<hash>
```

### 3

remote sources

download → verify MD5 → promote to cache → return path

If checksum mismatch:

```
remove bad remote source from DB
```

Self-healing registry.

---

# Remote Registration Model

Registering remote file:

```
fid register https://server/file.fastq
```

Workflow:

```
download temporary file
compute MD5
lookup registry
```

If new:

```
store cached local copy
register remote + cache location
```

If already known:

```
discard temp copy
register remote location only
```

---

# Cache Model

Cache location:

```
~/.fid/cache/<base62-id>
```

Config variable:

```
KEEP_CACHE = True
```

Behavior:

| value | behavior                   |
| ----- | -------------------------- |
| True  | reuse cached files         |
| False | purge cache before resolve |

Manual cleanup:

```
fid purge-cache
```

Cache entries behave exactly like normal local copies.

No special handling required.

---

# Metadata Model

Schema-free key/value metadata.

Examples:

```
sample_id=S17
instrument=NovaSeq
lane=4
project=KidneyStudy
```

Multiple attributes per file allowed.

---

# URI Scheme Model

fid identifiers use URI syntax:

```
fid://<ID>
```

Example:

```
fid://3D7p8Kf9Ls0ZxW2jQnRtYu
```

Remote locations use native schemes:

```
https://
ftp://
sftp://
```

Local files:

```
/absolute/path/file.fastq
```

No prefix required.

---

# Core Commands Summary

## Register file

```
fid register <path-or-url>
```

Examples:

```
fid register sample.fastq
fid register /data/sample.fastq
fid register https://server/sample.fastq
```

Returns:

```
fid://ABC123
```

Deduplicates automatically.

---

## Get ID without recomputing checksum

```
fid id <path>
```

Behavior:

| case               | action                  |
| ------------------ | ----------------------- |
| already registered | return ID               |
| not registered     | register then return ID |

Example:

```
fid id sample.fastq
```

Output:

```
fid://ABC123
```

---

## Resolve file

```
fid resolve fid://ABC123
```

Returns:

```
local filesystem path
```

Automatically downloads remote copy if needed.

Verifies checksum before returning.

---

## Verify local copies

```
fid verify fid://ABC123
```

Checks integrity:

```
OK
MISSING
MISMATCH
```

---

## Add metadata

```
fid meta set fid://ABC key value
```

Example:

```
fid meta set fid://ABC sample_id S17
```

---

## Retrieve metadata

```
fid meta get fid://ABC
```

Output:

```
sample_id=S17
project=KidneyStudy
```

---

## Remove metadata field

```
fid meta rm fid://ABC key
```

Example:

```
fid meta rm fid://ABC lane
```

---

## Metadata search

```
fid find key=value
```

Example:

```
fid find sample_id=S17
```

Returns:

```
fid://ABC123
fid://XYZ987
```

---

## List registry contents

```
fid list
```

Outputs grouped structure:

```
fid://ABC123
  /data/sample.fastq
  https://server/sample.fastq

fid://XYZ987
  /cache/XYZ987
```

Shows all storage locations.

---

## Purge cache

```
fid purge-cache
```

Deletes:

```
~/.fid/cache/*
```

---

# Collision Protection Model

On registration:

```
same MD5 + different file size
```

Triggers:

```
ERROR md5 collision
```

Registration aborted.

Extremely unlikely scenario.

---

# Prefix Resolution Model

Short IDs allowed:

```
fid resolve fid://ABC123
```

If unique → resolves.

If ambiguous:

```
prefix not unique
```

User must extend prefix.

---

# Integrity Model

Every remote download:

```
recompute MD5
verify identity
```

If mismatch:

```
remove remote location entry
```

Registry self-repairs automatically.

---

# Example Workflow

Register:

```
fid register sample.fastq
```

Copy ID:

```
fid://3D7p8Kf9Ls0ZxW2jQnRtYu
```

Resolve later:

```
fid resolve fid://3D7p8Kf9Ls0ZxW2j
```

Works even if file moved or remote-only.

---

# Conceptual Architecture Summary

fid separates:

### identity

```
fid://ABC123
```

from

### storage location

```
filesystem
cache
http
ftp
sftp
```

from

### metadata

```
sample_id=S17
instrument=Orbitrap
```

This enables:

* deduplication
* portability
* verification
* distributed storage
* reproducible pipelines
* GUI integrations (Finder, Explorer, Nautilus)
* notebook integrations
* workflow engines
* cloud mirroring
* remote caching layers

---

Ideas:

* Finder integration
* Spotlight metadata column
* drag-and-drop registration
* clipboard helpers
* R bindings
* Python API wrapper
* REST service mode
* shared lab registry mode

---

# Git Filter Setup (fid-git)

## Overview

`fid-git` is a git filter driver that automatically stores large files on a remote fid server and replaces them with lightweight `fid://` pointers in the git repository.

## Server Setup

First, ensure you have a fid server running:

```bash
# On server machine (e.g., SimonEiMac.scilifelab.se)
fid_api.py --port 8080

# Or as a LaunchDaemon (macOS)
sudo launchctl load /Library/LaunchDaemons/com.fid.server.plist
```

## Client Setup - New Repository

### Step 1: Install fid

```bash
# Clone fid repo
git clone https://github.com/simonelsasser/fid.git ~/GitHub/fid

# Install fid CLI and git filter
cd ~/GitHub/fid
sudo ./fid_install.sh

# Verify installation
fid --version
fid git --help
```

### Step 2: Configure Your Repository

```bash
# In your git repository
cd your-repo

# Create .fidconfig with server URL
cat > .fidconfig << 'EOF'
{
  "server": "http://SimonEiMac.scilifelab.se:8080"
}
EOF

# Create or edit .gitattributes to specify which files to manage
cat >> .gitattributes << 'EOF'

# Git filter for large files managed by fid
*.fastq.gz filter=fid
*.bam filter=fid
*.cram filter=fid
*.fastq filter=fid
*.fq filter=fid
*.sam filter=fid
*.vcf.gz filter=fid
*.bed filter=fid
*.gtf filter=fid
*.gff filter=fid
*.bam.bai filter=fid
*.cram.crai filter=fid
*.fastq.gz.csi filter=fid
*.fai filter=fid
*.gzi filter=fid
*.rai filter=fid
*.dict filter=fid
*.tbz filter=fid
*.tar.gz filter=fid
*.zip filter=fid
*.tar filter=fid
*.gz filter=fid
*.bz2 filter=fid
*.xz filter=fid
*.zst filter=fid
*.lz4 filter=fid
*.7z filter=fid
*.rar filter=fid
*.bin filter=fid
*.dat filter=fid
*.db filter=fid
*.sqlite filter=fid
*.h5 filter=fid
*.hdf5 filter=fid
*.hdf filter=fid
*.he5 filter=fid
*.nc filter=fid
*.netcdf filter=fid
*.nc4 filter=fid
*.cdf filter=fid
*.parquet filter=fid
*.feather filter=fid
*.arrow filter=fid
*.orc filter=fid
*.avro filter=fid
*.pb filter=fid
*.protobuf filter=fid
*.pkl filter=fid
*.pickle filter=fid
*.joblib filter=fid
*.npy filter=fid
*.npz filter=fid
*.pt filter=fid
*.pth filter=fid
*.onnx filter=fid
*.mlmodel filter=fid
*.tflite filter=fid
*.tvm filter=fid
*.xgb filter=fid
*.bst filter=fid
*.lgb filter=fid
*.catboost filter=fid
*.model filter=fid
*.weights filter=fid
*.params filter=fid
*.meta filter=fid
*.index filter=fid
*.checkpoint filter=fid
*.ckpt filter=fid
*.ptl filter=fid
*.bin filter=fid
*.pbtxt filter=fid
*.graphdef filter=fid
*.frozen filter=fid
*.saved_model filter=fid
*.mlmodel filter=fid
*.coreml filter=fid
*.tflite filter=fid
*.lite filter=fid
*.tvm filter=fid
*.json filter=fid
*.yaml filter=fid
*.yml filter=fid
*.toml filter=fid
*.ini filter=fid
*.cfg filter=fid
*.conf filter=fid
*.config filter=fid
*.properties filter=fid
*.props filter=fid
*.settings filter=fid
*.prefs filter=fid
*.pref filter=fid
*.option filter=fid
*.options filter=fid
*.rc filter=fid
*.profile filter=fid
*.bashrc filter=fid
*.zshrc filter=fid
*.fish filter=fid
*.sh filter=fid
*.bash filter=fid
*.zsh filter=fid
*.csh filter=fid
*.tcsh filter=fid
*.ksh filter=fid
*.mksh filter=fid
*.ash filter=fid
*.dash filter=fid
*.posix filter=fid
*.command filter=fid
*.bat filter=fid
*.cmd filter=fid
*.btm filter=fid
*.ps1 filter=fid
*.psm1 filter=fid
*.psd1 filter=fid
*.ps1xml filter=fid
*.psc1 filter=fid
*.psc2 filter=fid
*.pssc filter=fid
*.psrc filter=fid
*.cdxml filter=fid
*.cdiff filter=fid
*.wsf filter=fid
*.wsc filter=fid
*.vbs filter=fid
*.vbe filter=fid
*.js filter=fid
*.jse filter=fid
*.ts filter=fid
*.tsx filter=fid
*.jsx filter=fid
*.coffee filter=fid
*.cake filter=fid
*.csx filter=fid
*.fsx filter=fid
*.fsi filter=fid
*.fsscript filter=fid
*.r filter=fid
*.rmd filter=fid
*.rmarkdown filter=fid
*.rnw filter=fid
*.rtex filter=fid
*.rhtml filter=fid
*.rxml filter=fid
*.rpy filter=fid
*.ipynb filter=fid
*.py filter=fid
*.pyw filter=fid
*.pyi filter=fid
*.pyc filter=fid
*.pyo filter=fid
*.pyd filter=fid
*.pyz filter=fid
*.pyzw filter=fid
*.egg filter=fid
*.whl filter=fid
*.dist-info filter=fid
*.egg-info filter=fid
*.pth filter=fid
*.so filter=fid
*.dylib filter=fid
*.dll filter=fid
*.pyd filter=fid
*.bundle filter=fid
*.framework filter=fid
*.o filter=fid
*.obj filter=fid
*.a filter=fid
*.lib filter=fid
*.exp filter=fid
*.map filter=fid
*.ilk filter=fid
*.pdb filter=fid
*.idb filter=fid
*.tds filter=fid
*.rsc filter=fid
*.res filter=fid
*.rc2 filter=fid
*.def filter=fid
*.mc filter=fid
*.mft filter=fid
*.manifest filter=fid
*.sxs filter=fid
*.mui filter=fid
*.satellite filter=fid
*.nlm filter=fid
*.nls filter=fid
*.cpi filter=fid
*.cpd filter=fid
*.cpx filter=fid
*.cpl filter=fid
*.drv filter=fid
*.sys filter=fid
*.vxd filter=fid
*.ocx filter=fid
*.tlb filter=fid
*.olb filter=fid
*.exe filter=fid
*.com filter=fid
*.scr filter=fid
*.pif filter=fid
*.msi filter=fid
*.msp filter=fid
*.msm filter=fid
*.mst filter=fid
*.msu filter=fid
*.cab filter=fid
*.appx filter=fid
*.appxbundle filter=fid
*.msix filter=fid
*.msixbundle filter=fid
*.dmg filter=fid
*.pkg filter=fid
*.deb filter=fid
*.rpm filter=fid
*.apk filter=fid
*.aab filter=fid
*.ipa filter=fid
*.xap filter=fid
*.app filter=fid
*.appex filter=fid
*.framework filter=fid
*.xcframework filter=fid
*.storyboardc filter=fid
*.nib filter=fid
*.xib filter=fid
*.strings filter=fid
*.plist filter=fid
*.mobileprovision filter=fid
*.entitlements filter=fid
*.exportOptions filter=fid
*.xcassets filter=fid
*.xcconfig filter=fid
*.xcodeproj filter=fid
*.xcworkspace filter=fid
*.playground filter=fid
*.swiftdoc filter=fid
*.swiftmodule filter=fid
*.swiftinterface filter=fid
*.pcm filter=fid
*.clangmodule filter=fid
*.modulemap filter=fid
*.h filter=fid
*.hpp filter=fid
*.hxx filter=fid
*.hh filter=fid
*.h++ filter=fid
*.inl filter=fid
*.ipp filter=fid
*.ixx filter=fid
*.txx filter=fid
*.tcc filter=fid
*.tpp filter=fid
*.cpp filter=fid
*.cc filter=fid
*.cxx filter=fid
*.c++ filter=fid
*.cp filter=fid
*.m filter=fid
*.mm filter=fid
*.metal filter=fid
*.shader filter=fid
*.glsl filter=fid
*.hlsl filter=fid
*.cg filter=fid
*.vert filter=fid
*.frag filter=fid
*.geom filter=fid
*.tesc filter=fid
*.tese filter=fid
*.comp filter=fid
*.mesh filter=fid
*.task filter=fid
*.rgen filter=fid
*.rint filter=fid
*.rahit filter=fid
*.rchit filter=fid
*.rmiss filter=fid
*.rcall filter=fid
*.asm filter=fid
*.s filter=fid
*.S filter=fid
*.ms filter=fid
*.il filter=fid
*.rb filter=fid
*.rbw filter=fid
*.rbx filter=fid
*.rake filter=fid
*.gemspec filter=fid
*.podspec filter=fid
*.rbuild filter=fid
*.m4 filter=fid
*.ac filter=fid
*.am filter=fid
*.in filter=fid
*.template filter=fid
*.in.h filter=fid
*.in.c filter=fid
*.in.cpp filter=fid
*.pc.in filter=fid
*.spec filter=fid
*.desktop filter=fid
*.service filter=fid
*.target filter=fid
*.socket filter=fid
*.timer filter=fid
*.path filter=fid
*.mount filter=fid
*.automount filter=fid
*.swap filter=fid
*.slice filter=fid
*.scope filter=fid
*.device filter=fid
*.unit filter=fid
*.link filter=fid
*.network filter=fid
*.netdev filter=fid
*.wpa_supplicant filter=fid
*.hosts filter=fid
*.hostname filter=fid
*.hosts.allow filter=fid
*.hosts.deny filter=fid
*.resolv.conf filter=fid
*.nsswitch.conf filter=fid
*.ld.so.conf filter=fid
*.ld.so.cache filter=fid
*.fstab filter=fid
*.mtab filter=fid
*.crypttab filter=fid
*.inittab filter=fid
*.issue filter=fid
*.motd filter=fid
*.environment filter=fid
*.pam.d filter=fid
*.security filter=fid
*.limits.conf filter=fid
*.sysctl.conf filter=fid
*.sysctl.d filter=fid
*.modprobe.d filter=fid
*.modules-load.d filter=fid
*.tmpfiles.d filter=fid
*.systemd filter=fid
*.udev filter=fid
*.rules filter=fid
*.hwdb filter=fid
*.hwdb.bin filter=fid
*.conf filter=fid
*.cfg filter=fid
*.config filter=fid
*.ini filter=fid
*.inf filter=fid
*.reg filter=fid
*.url filter=fid
*.lnk filter=fid
*.job filter=fid
*.msc filter=fid
*.msc filter=fid
*.cpl filter=fid
*.scr filter=fid
*.pif filter=fid
*.cmd filter=fid
*.bat filter=fid
*.btm filter=fid
*.vbs filter=fid
*.vbe filter=fid
*.js filter=fid
*.jse filter=fid
*.wsf filter=fid
*.wsc filter=fid
*.wsh filter=fid
*.ps1 filter=fid
*.psm1 filter=fid
*.psd1 filter=fid
*.ps1xml filter=fid
*.psc1 filter=fid
*.psc2 filter=fid
*.pssc filter=fid
*.psrc filter=fid
*.cdxml filter=fid
*.cdiff filter=fid
*.mof filter=fid
*.mfl filter=fid
*.mofc filter=fid
*.wbmp filter=fid
*.wim filter=fid
*.swm filter=fid
*.esd filter=fid
*.ffu filter=fid
*.vhdx filter=fid
*.vhd filter=fid
*.avhdx filter=fid
*.avhd filter=fid
*.diff filter=fid
*.parent filter=fid
*.checkpoint filter=fid
*.rct filter=fid
*.mcx filter=fid
*.mof filter=fid
*.mfl filter=fid
*.mofc filter=fid
*.wbmp filter=fid
*.wim filter=fid
*.swm filter=fid
*.esd filter=fid
*.ffu filter=fid
*.vhdx filter=fid
*.vhd filter=fid
*.avhdx filter=fid
*.avhd filter=fid
*.diff filter=fid
*.parent filter=fid
*.checkpoint filter=fid
*.rct filter=fid
*.mcx filter=fid
EOF

# Initialize git filter
fid git init

# Commit configuration files
git add .fidconfig .gitattributes
git commit -m "Configure fid git filter for large file management"
```

### Step 3: Add Large Files

```bash
# Add files normally - they will be automatically uploaded to server
git add path/to/large_file.fastq.gz
git commit -m "Add large file managed by fid"

# The file content is stored on the server
# Git only stores a lightweight fid:// pointer
```

## Cloning a fid-Enabled Repository

### Important: Two-Step Checkout

When cloning a repository that uses fid-git, files will initially appear as `fid://` pointers:

```bash
# Step 1: Clone the repository
git clone <repository-url>
cd repository

# Step 2: Configure fid server URL
cat > .fidconfig << 'EOF'
{
  "server": "http://SimonEiMac.scilifelab.se:8080"
}
EOF

# Step 3: Initialize git filter
fid git init

# Step 4: Checkout files to trigger smudge filter
# This downloads actual file content from server
git checkout HEAD -- .

# Or for specific files:
git checkout HEAD -- path/to/large_file.fastq.gz
```

### Why Two Steps?

1. **Initial clone** downloads git objects, which contain `fid://` pointers (not actual file content)
2. **`git checkout HEAD -- .`** triggers the smudge filter, which:
   - Reads `fid://` pointers from git index
   - Downloads actual content from fid server
   - Writes real files to working directory

### Verify Smudge Worked

```bash
# Check file type (should be actual content, not ASCII text)
file path/to/large_file.fastq.gz
# Expected: gzip compressed data (not ASCII text)

# Check file size (should be real size, not ~20 bytes)
ls -lh path/to/large_file.fastq.gz
# Expected: actual file size (MB/GB), not pointer size

# View file content (should be binary, not fid:// text)
head path/to/large_file.fastq.gz
# Expected: binary data (not fid://ABC123...)
```

### Troubleshooting

**Files still show as fid:// pointers:**

```bash
# Force smudge on all tracked files
git ls-files | xargs git checkout HEAD --

# Or re-smudge specific file
git checkout HEAD -- path/to/file

# Verify server is reachable
curl http://SimonEiMac.scilifelab.se:8080/list

# Check .fidconfig exists and has correct server URL
cat .fidconfig
```

**Download fails:**

```bash
# Test resolve manually
fid resolve fid://ABC123...

# Check server logs
ssh SimonEiMac.scilifelab.se "tail -50 /var/log/fid_server_error.log"

# Verify fid exists on server
curl "http://SimonEiMac.scilifelab.se:8080/resolve?fid=ABC123..."
```

## How It Works

### Clean Filter (git add)

When you run `git add file.fastq.gz`:

1. Git calls `fid_git.py clean file.fastq.gz`
2. Filter reads file content
3. Calculates MD5 checksum → fid
4. Uploads to server: `POST /upload?fid=XXX&filename=file.fastq.gz`
5. Server validates and stores file
6. Filter writes `fid://XXX` pointer to git index

### Smudge Filter (git checkout)

When you run `git checkout HEAD -- file.fastq.gz`:

1. Git reads `fid://XXX` pointer from index
2. Calls `fid_git.py smudge`
3. Filter resolves fid locally or downloads from server
4. Writes actual file content to working directory

### Self-Healing

The `/resolve` endpoint automatically cleans up stale database entries:
- If file is deleted from server, resolve removes the path from database
- Next resolve attempt returns 404 (fid not found)
- Keeps database consistent with actual file system state

## Best Practices

1. **Always commit `.fidconfig`** - Required for smudge to work
2. **Configure server before checkout** - Set up `.fidconfig` before running `git checkout`
3. **Use meaningful .gitattributes** - Only filter large binary files, not source code
4. **Test smudge after clone** - Always run `git checkout HEAD -- .` after cloning
5. **Keep server accessible** - Clients need server access to smudge files

## Migration from Existing Repositories

If you have an existing repository with large files:

```bash
# 1. Configure fid (as above)
# Create .fidconfig and .gitattributes
fid git init

# 2. Renormalize existing files
git add --renormalize .

# 3. Commit the changes
git commit -m "Migrate large files to fid storage"

# 4. Push to remote
git push
```

**Warning:** This rewrites git history for affected files. Consider using `git filter-branch` or `git filter-repo` for complete migration.
