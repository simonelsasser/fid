# Fid Error Handling Guide

## Overview

Fid uses a clean, unambiguous error handling strategy designed for easy integration with other tools and scripts.

## Output Strategy

### Success
- **stdout**: Result (fid or file path)
- **stderr**: (empty)
- **exit code**: 0

```bash
$ fid id file.txt
ABC123...
$ echo $?
0
```

### Error
- **stdout**: (empty)
- **stderr**: `ERROR:TYPE`
- **exit code**: Non-zero (typically 1)

```bash
$ fid id /missing/file.txt
$ echo $?
1
$ fid id /missing/file.txt 2>&1
ERROR:NOT_FOUND
```

## Error Types

| Error Type | When | Recovery |
|------------|------|----------|
| `NOT_FOUND` | File or fid doesn't exist | Register file or check fid |
| `SIZE_MISMATCH` | File size doesn't match stored size | Re-register file |
| `NOT_UNIQUE` | Multiple fids match prefix | Use more specific fid |
| `REGISTER_FAILED` | Cannot register file | Check permissions, disk space |
| `IO_ERROR` | Cannot read file | Check permissions, file exists |

## Usage Examples

### Shell Scripts

**Simple check (empty means error):**
```bash
fid=$(fid id file.txt)
if [ -z "$fid" ]; then
    echo "Failed to get fid"
    exit 1
fi
echo "Got fid: $fid"
```

**Check error type:**
```bash
fid=$(fid id --check-size file.txt)
error=$(fid id --check-size file.txt 2>&1 >/dev/null)

if [ "$error" = "ERROR:SIZE_MISMATCH" ]; then
    echo "File has changed, re-registering..."
    fid=$(fid id --register file.txt)
elif [ "$error" = "ERROR:NOT_FOUND" ]; then
    echo "File not registered"
    fid=$(fid id --register file.txt)
fi
```

**Exit code check:**
```bash
if fid id --check-size file.txt; then
    echo "Size OK"
else
    echo "Validation failed"
    exit 1
fi
```

### Python Integration

**Basic usage:**
```python
import subprocess

def get_fid(filepath):
    result = subprocess.run(
        ["fid", "id", filepath],
        capture_output=True, text=True
    )
    
    if result.returncode != 0:
        raise Exception(f"fid error: {result.stderr.strip()}")
    
    return result.stdout.strip()

# Usage
try:
    fid = get_fid("file.txt")
    print(f"Got fid: {fid}")
except Exception as e:
    print(f"Error: {e}")
```

**With size validation:**
```python
def get_fid_checked(filepath):
    result = subprocess.run(
        ["fid", "id", "--check-size", filepath],
        capture_output=True, text=True
    )
    
    if result.returncode != 0:
        error = result.stderr.strip()
        if error == "ERROR:SIZE_MISMATCH":
            print(f"Warning: {filepath} has changed")
            return None
        elif error == "ERROR:NOT_FOUND":
            print(f"Info: {filepath} not registered")
            return None
        else:
            raise Exception(f"fid error: {error}")
    
    return result.stdout.strip()
```

**Parse error type:**
```python
def run_fid_command(args):
    result = subprocess.run(
        ["fid"] + args,
        capture_output=True, text=True
    )
    
    if result.returncode != 0:
        error = result.stderr.strip()
        if error.startswith("ERROR:"):
            error_type = error.split(":")[1]
            return {"success": False, "error_type": error_type}
        return {"success": False, "error": error}
    
    return {"success": True, "output": result.stdout.strip()}

# Usage
result = run_fid_command(["id", "--check-size", "file.txt"])
if not result["success"]:
    if result.get("error_type") == "SIZE_MISMATCH":
        print("File changed!")
```

### Git Filter Integration

**Clean filter (upload):**
```python
import subprocess

def clean_filter(filepath):
    # Get fid for file
    result = subprocess.run(
        ["fid", "id", "--register", filepath],
        capture_output=True, text=True
    )
    
    # Empty stdout means error
    if not result.stdout.strip():
        print(f"fid-git: error getting fid: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    
    fid = result.stdout.strip()
    
    # Check if fid exists on server
    # ... (server check logic)
    
    return fid
```

**Smudge filter (download):**
```python
def smudge_filter(fid):
    # Resolve fid to local path
    result = subprocess.run(
        ["fid", "resolve", f"fid://{fid}"],
        capture_output=True, text=True
    )
    
    if not result.stdout.strip():
        # Not found locally, download from server
        download_from_server(fid)
        return
    
    local_path = result.stdout.strip()
    
    # Copy from local path
    shutil.copy(local_path, target_path)
```

## Best Practices

### 1. Always Check stdout
```bash
# Good
fid=$(fid id file.txt)
if [ -z "$fid" ]; then
    # Handle error
fi

# Bad - ignores empty output
fid id file.txt > /dev/null
```

### 2. Capture stderr for Debugging
```bash
# Capture both
output=$(fid id file.txt 2>&1)
if [ $? -ne 0 ]; then
    echo "Error: $output"
fi

# Or separate
fid=$(fid id file.txt)
error=$(fid id file.txt 2>&1 >/dev/null)
if [ -n "$error" ]; then
    echo "Error type: $error"
fi
```

### 3. Use Exit Codes in Pipelines
```bash
# Good - exit code propagates
fid id file.txt && echo "Success" || echo "Failed"

# Good - explicit check
if fid id --check-size file.txt; then
    process_file
else
    handle_error
fi
```

### 4. Handle Specific Errors
```python
result = subprocess.run(["fid", "id", "--check-size", file], 
                       capture_output=True, text=True)

if result.returncode != 0:
    error = result.stderr.strip()
    
    if error == "ERROR:SIZE_MISMATCH":
        # File changed, re-register
        subprocess.run(["fid", "register", file])
    elif error == "ERROR:NOT_FOUND":
        # New file, register it
        subprocess.run(["fid", "register", file])
    else:
        # Unknown error
        raise Exception(error)
```

## Commands with Error Handling

### `fid id`
```bash
# Success
$ fid id file.txt
ABC123...

# File not found
$ fid id /missing.txt
ERROR:NOT_FOUND

# Size mismatch
$ fid id --check-size modified.txt
ERROR:SIZE_MISMATCH
```

### `fid resolve`
```bash
# Success
$ fid resolve ABC123...
/path/to/file.txt

# Fid not found
$ fid resolve XYZ789...
ERROR:NOT_FOUND

# Size mismatch
$ fid resolve --check-size ABC123...
ERROR:SIZE_MISMATCH

# Ambiguous prefix
$ fid resolve ABC
ERROR:NOT_UNIQUE
```

### `fid register`
```bash
# Success
$ fid register file.txt
ABC123...

# File missing
$ fid register /missing.txt
(no output to stdout)
ERROR:IO_ERROR  # or similar
```

## Migration Guide

### From Old Warning Messages

**Old behavior:**
```bash
$ fid id --check-size file.txt
fid id: warning: size mismatch for file.txt (expected 100, got 200)
ABC123...
```

**New behavior:**
```bash
$ fid id --check-size file.txt
ERROR:SIZE_MISMATCH
(no stdout output)
```

**Update your scripts:**
```bash
# Old script (checks stderr for warning)
fid id file.txt 2>&1 | grep -q "warning" && echo "Changed"

# New script (checks exit code)
fid id --check-size file.txt || echo "Changed"

# Or check error type
error=$(fid id --check-size file.txt 2>&1 >/dev/null)
[ "$error" = "ERROR:SIZE_MISMATCH" ] && echo "Changed"
```

## Troubleshooting

### No Output at All
```bash
# Check if command succeeded
fid id file.txt
echo "Exit code: $?"  # Should be 0

# Check stderr separately
fid id file.txt 2>&1 >/dev/null
```

### Unexpected Error Type
```bash
# Get full error details
fid id file.txt 2>&1

# Common issues:
# - ERROR:NOT_FOUND → File not registered, use: fid register file.txt
# - ERROR:SIZE_MISMATCH → File changed, use: fid register file.txt
# - ERROR:NOT_UNIQUE → Fid prefix ambiguous, use full fid
```

### Exit Code Always 0
```bash
# Make sure you're checking after the command
fid id file.txt
echo $?  # Correct

# Not like this:
echo $(fid id file.txt) $?  # Wrong, $? is from echo
```

## Summary

- **Empty stdout** = error occurred
- **ERROR:TYPE** on stderr = specific error
- **Exit code 0** = success, **non-zero** = error
- Check stdout for results, stderr for error details
- Use exit codes for flow control
- Parse error types for specific handling

This design makes fid easy to integrate with other tools while providing clear error information when needed.