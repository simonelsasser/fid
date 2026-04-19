Excellent idea. A clean **design + command summary spec** will make future integrations (Finder, Spotlight, Linux desktop, R bindings, pipelines, web APIs, etc.) much easier to discuss in separate threads.

Below is a concise but complete **fid v1 architecture reference**.

---

# fid — Design Specification (Current Version)

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

Properties:

| property     | meaning                   |
| ------------ | ------------------------- |
| immutable    | content-addressable       |
| portable     | independent of filesystem |
| deduplicated | identical files share ID  |
| verifiable   | MD5 integrity check       |
| shortenable  | prefix lookup supported   |

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

This spec is now stable enough to support:

next threads on

* Finder integration
* Spotlight metadata column
* drag-and-drop registration
* clipboard helpers
* R bindings
* Python API wrapper
* REST service mode
* shared lab registry mode
