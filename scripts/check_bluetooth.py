#!/usr/bin/env python3
"""Standalone Bluetooth adapter diagnostic and power-up utility.

Run this script anytime to:
1. Verify the USB Bluetooth antenna is plugged in and recognized by the kernel.
2. Unblock rfkill if soft-blocked.
3. Verify the systemd bluetooth service is active.
4. Power ON the adapter via bluetoothctl (turning on the blue indicator LED).
5. Report the active BD Address (MAC) used for all MetaWear connections.

Usage:
  ./metawear_39_env/bin/python scripts/check_bluetooth.py
"""

import os
import sys
import subprocess
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.core.bluetooth_utils import (
    ensure_bluetooth_ready,
    is_service_active,
    unblock_rfkill,
    get_active_adapter_info,
    BluetoothAdapterError,
)


def check_lsusb_dongle():
    """Inspect lsusb for Realtek or generic Bluetooth USB dongles."""
    try:
        out = subprocess.check_output(["lsusb"], text=True, stderr=subprocess.DEVNULL)
        lines = [line for line in out.strip().splitlines() if any(k in line.lower() for k in ["bluetooth", "realtek", "0bda:"])]
        return lines
    except Exception:
        return []


def main():
    print("=" * 65)
    print("   MetaWear USB Bluetooth Antenna Diagnostic & Power Utility")
    print("=" * 65)

    # 1. Check USB bus enumeration
    print("\n1. Inspecting USB Bus Devices (lsusb)...")
    dongles = check_lsusb_dongle()
    if dongles:
        for d in dongles:
            print(f"   ✓ Detected: {d}")
    else:
        print("   ⚠ Warning: No obvious Bluetooth USB devices found in `lsusb`.")
        print("     Ensure your USB Bluetooth antenna is firmly inserted.")

    # 2. Check rfkill
    print("\n2. Checking rfkill soft-block status...")
    unblock_ok = unblock_rfkill()
    if unblock_ok:
        print("   ✓ rfkill unblock bluetooth: executed successfully.")
    else:
        print("   • rfkill unblock: skipped or returned non-zero.")

    # 3. Check systemd bluetooth service
    print("\n3. Checking systemd Bluetooth Service...")
    svc_active = is_service_active()
    if svc_active:
        print("   ✓ Bluetooth system service is ACTIVE.")
    else:
        print("   ⚠ Warning: Bluetooth system service is INACTIVE.")
        print("     Try: sudo systemctl restart bluetooth")

    # 4. Detect and power on controller
    print("\n4. Detecting Controller and Powering ON Radio...")
    try:
        info = ensure_bluetooth_ready(auto_power_on=True, verbose=True)
        mac = info["mac"]
        name = info.get("name", "Unknown")
        is_powered = info.get("powered", False)

        print(f"\n   -----------------------------------------------------")
        print(f"   ✓ ACTIVE CONTROLLER MAC:   {mac}")
        print(f"   ✓ CONTROLLER NAME:         {name}")
        print(f"   ✓ CONTROLLER POWER STATE:  {'POWERED ON' if is_powered else 'NOT POWERED'}")
        print(f"   ✓ BLUE ANTENNA LED:        {'ACTIVE / ILLUMINATED' if is_powered else 'OFF'}")
        print(f"   -----------------------------------------------------")
        print("\n✓ SUCCESS: Bluetooth adapter is fully operational and ready!")
        print("  You can now run any test or trial command without FD_SETSIZE errors:")
        print("    ./metawear_39_env/bin/python pipeline/cli.py test")
        print("    ./metawear_39_env/bin/python pipeline/cli.py record")
        return 0

    except BluetoothAdapterError as e:
        print(f"\n✗ Error: {e}")
        return 1
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
