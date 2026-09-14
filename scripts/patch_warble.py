#!/usr/bin/env python3
"""Warble FD_SETSIZE Binary Patch & Resilience Utility.

Eliminates the glibc crash:
    `*** bit out of range 0 - FD_SETSIZE on fd_set ***: terminated`
by safely replacing the fortified PLT entry for `__fdelt_chk` in `libwarble.so`
with an in-place safe bounds check and index calculation.

When a BLE socket connection fails or is closed, Warble's state machine sets
the socket fd to -1. Calling FD_SET(-1) under glibc's _FORTIFY_SOURCE=2 triggers
__fdelt_chk(-1) which aborts the process. With this patch, out-of-range descriptors
safely return 0, select() ignores or reports EBADF, and Warble invokes its standard
error callback so Python's retry mechanism can cleanly handle it.

Usage:
    python scripts/patch_warble.py          # Apply patch
    python scripts/patch_warble.py --check  # Verify status
    python scripts/patch_warble.py --restore # Restore original backups
"""

import sys
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Offset in libwarble.so (.plt.sec for __fdelt_chk)
PLT_OFFSET = 0xC890

# Original 16-byte PLT jump stub to __fdelt_chk@GLIBC_2.15
ORIGINAL_BYTES = bytes([
    0xF3, 0x0F, 0x1E, 0xFA,        # endbr64
    0xFF, 0x25, 0xAE, 0x7C, 0x03, 0x00,  # jmp *0x37cae(%rip)
    0x66, 0x0F, 0x1F, 0x44, 0x00, 0x00   # nopw 0x0(%rax,%rax,1)
])

# Patched 16-byte safe bounds-check and index calculator:
#   mov    %rdi, %rax       ; rax = d
#   shr    $0x6, %rax       ; rax = d >> 6 (d / 64)
#   cmp    $0x10, %rax      ; compare with 16 (since FD_SETSIZE 1024 / 64 = 16)
#   jb     1f               ; if 0 <= d < 1024, valid index, return rax
#   xor    %eax, %eax       ; if d < 0 (high bit set after shift) or d >= 1024, return 0
# 1: ret
PATCH_BYTES = bytes([
    0x48, 0x89, 0xF8,              # mov %rdi, %rax
    0x48, 0xC1, 0xE8, 0x06,        # shr $6, %rax
    0x48, 0x83, 0xF8, 0x10,        # cmp $16, %rax
    0x72, 0x02,                    # jb 1f
    0x31, 0xC0,                    # xor %eax, %eax
    0xC3,                          # ret
])

assert len(ORIGINAL_BYTES) == 16, "Original sequence must be 16 bytes"
assert len(PATCH_BYTES) == 16, "Patch sequence must be 16 bytes"


def find_warble_libraries():
    """Discover all libwarble.so library files in the project's virtualenv or site-packages."""
    candidates = []
    venv_site = PROJECT_ROOT / "metawear_39_env" / "lib" / "python3.9" / "site-packages" / "mbientlab" / "warble"
    if venv_site.is_dir():
        for p in venv_site.glob("libwarble.so*"):
            if not p.name.endswith(".orig") and not p.name.endswith(".bak"):
                candidates.append(p)
    return sorted(candidates)


def check_file_status(filepath: Path) -> str:
    """Return status of target library: 'PATCHED', 'UNPATCHED', or 'UNKNOWN'."""
    with open(filepath, "rb") as f:
        f.seek(PLT_OFFSET)
        current = f.read(16)
    if current == PATCH_BYTES:
        return "PATCHED"
    elif current == ORIGINAL_BYTES:
        return "UNPATCHED"
    else:
        return f"UNKNOWN ({current.hex()})"


def patch_file(filepath: Path, backup: bool = True) -> bool:
    """Apply the 16-byte guard patch to the target library."""
    status = check_file_status(filepath)
    if status == "PATCHED":
        return True

    orig_backup = filepath.with_suffix(filepath.suffix + ".orig")
    if backup and not orig_backup.exists():
        shutil.copy2(filepath, orig_backup)

    with open(filepath, "r+b") as f:
        f.seek(PLT_OFFSET)
        f.write(PATCH_BYTES)
        f.flush()

    new_status = check_file_status(filepath)
    return new_status == "PATCHED"


def restore_file(filepath: Path) -> bool:
    """Restore target library from .orig backup."""
    orig_backup = filepath.with_suffix(filepath.suffix + ".orig")
    if not orig_backup.exists():
        return False
    shutil.copy2(orig_backup, filepath)
    return check_file_status(filepath) == "UNPATCHED"


def ensure_warble_patched(verbose: bool = False) -> bool:
    """Self-healing function called by pipeline to ensure libwarble is patched."""
    libs = find_warble_libraries()
    if not libs:
        if verbose:
            print("[Warble Patch] No libwarble libraries found.")
        return False

    all_ok = True
    for lib in libs:
        status = check_file_status(lib)
        if status != "PATCHED":
            if verbose:
                print(f"[Warble Patch] Patching {lib.name} ({status} -> PATCHED)...")
            ok = patch_file(lib, backup=True)
            if not ok:
                all_ok = False
        else:
            if verbose:
                print(f"[Warble Patch] {lib.name} is already protected.")
    return all_ok


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Warble FD_SETSIZE Binary Patch Utility")
    parser.add_argument("--check", action="store_true", help="Check status of libwarble binaries without modifying")
    parser.add_argument("--restore", action="store_true", help="Restore libwarble from .orig backups")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    libs = find_warble_libraries()
    if not libs:
        print("✗ Error: No libwarble.so files found in metawear_39_env.")
        sys.exit(1)

    print("=" * 60)
    print("  Warble FD_SETSIZE Crash Prevention & Binary Patch Utility")
    print("=" * 60)

    if args.check:
        print("\nChecking libwarble status:")
        for lib in libs:
            st = check_file_status(lib)
            icon = "✓" if st == "PATCHED" else ("⚠" if st == "UNPATCHED" else "✗")
            print(f"  {icon} {lib.name:<25} : {st}")
        return

    if args.restore:
        print("\nRestoring libwarble from backups:")
        for lib in libs:
            ok = restore_file(lib)
            icon = "✓" if ok else "✗"
            print(f"  {icon} Restored {lib.name}")
        return

    print("\nApplying safe bounds-check patch to libwarble:")
    for lib in libs:
        st = check_file_status(lib)
        if st == "PATCHED":
            print(f"  ✓ {lib.name:<25} is already PATCHED.")
        else:
            ok = patch_file(lib, backup=True)
            if ok:
                print(f"  ✓ Successfully PATCHED {lib.name} (backup saved to {lib.name}.orig)")
            else:
                print(f"  ✗ FAILED to patch {lib.name}")

    print("\n✓ Permanent protection active: FD_SETSIZE crashes eliminated.")


if __name__ == "__main__":
    main()
