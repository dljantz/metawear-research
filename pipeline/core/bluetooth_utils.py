"""Bluetooth management and hardware readiness utilities.

Handles:
- Automatic detection of the active Bluetooth controller MAC and HCI device (e.g. hci1).
- Unblocking rfkill if soft-blocked.
- Powering on the Bluetooth adapter via bluetoothctl (turning on the physical blue LED indicator).
- Preventing FD_SETSIZE crashes by ensuring Warble is never called without a valid adapter.
"""

import re
import time
import subprocess
from typing import Optional, Dict, Any


class BluetoothAdapterError(RuntimeError):
    """Raised when no functional Bluetooth adapter can be detected or initialized."""
    pass


def unblock_rfkill(timeout_s: float = 2.0) -> bool:
    """Unblock Bluetooth devices via rfkill if soft-blocked."""
    try:
        res = subprocess.run(
            ["rfkill", "unblock", "bluetooth"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_s,
        )
        return res.returncode == 0
    except Exception:
        return False


def is_service_active(timeout_s: float = 2.0) -> bool:
    """Check if the systemd bluetooth service is running."""
    try:
        res = subprocess.run(
            ["systemctl", "is-active", "bluetooth"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout_s,
        )
        return res.stdout.strip() == "active"
    except Exception:
        return False


def power_on_adapter(mac: Optional[str] = None, timeout_s: float = 3.0) -> bool:
    """Send HCI power on command via bluetoothctl to illuminate the LED and activate radio."""
    # If mac is specified, feed select and power on via stdin to bluetoothctl
    if mac:
        try:
            input_str = f"select {mac}\npower on\nquit\n"
            res = subprocess.run(
                ["bluetoothctl"],
                input=input_str,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=timeout_s,
            )
            return res.returncode == 0
        except Exception:
            return False

    # Otherwise run standard non-interactive 'power on' for default controller
    try:
        res = subprocess.run(
            ["bluetoothctl", "power", "on"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout_s,
        )
        return res.returncode == 0
    except Exception:
        return False


def get_active_adapter_info(timeout_s: float = 3.0) -> Optional[Dict[str, Any]]:
    """Query bluetoothctl and hciconfig for active controller details."""
    # 1. Try bluetoothctl show (gives default controller status and power state)
    try:
        out = subprocess.check_output(
            ["bluetoothctl", "show"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=timeout_s,
        )
        mac_m = re.search(r"Controller\s+([0-9A-Fa-f:]{17})", out)
        powered_m = re.search(r"Powered:\s+(yes|no)", out, re.IGNORECASE)
        name_m = re.search(r"Name:\s+(.+)", out)
        if mac_m:
            mac = mac_m.group(1).upper()
            powered = (powered_m.group(1).lower() == "yes") if powered_m else False
            name = name_m.group(1).strip() if name_m else "Unknown"
            return {
                "mac": mac,
                "powered": powered,
                "name": name,
                "source": "bluetoothctl show",
            }
    except Exception:
        pass

    # 2. Try bluetoothctl list (enumerates controllers if show failed)
    try:
        out = subprocess.check_output(
            ["bluetoothctl", "list"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=timeout_s,
        )
        lines = out.strip().splitlines()
        for line in lines:
            m = re.search(r"Controller\s+([0-9A-Fa-f:]{17})\s*(.*)", line)
            if m:
                mac = m.group(1).upper()
                name = m.group(2).strip()
                return {
                    "mac": mac,
                    "powered": None,
                    "name": name,
                    "source": "bluetoothctl list",
                }
    except Exception:
        pass

    # 3. Fallback: Try hciconfig
    try:
        out = subprocess.check_output(
            ["hciconfig"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=timeout_s,
        )
        devices = out.split("\n\n")
        for dev_block in devices:
            dev_m = re.search(r"^(hci\d+):", dev_block, re.MULTILINE)
            mac_m = re.search(r"BD Address:\s*([0-9A-Fa-f:]{17})", dev_block)
            if mac_m:
                dev_name = dev_m.group(1) if dev_m else "hci"
                is_up = "UP RUNNING" in dev_block or "UP" in dev_block
                return {
                    "mac": mac_m.group(1).upper(),
                    "hci": dev_name,
                    "powered": is_up,
                    "name": dev_name,
                    "source": "hciconfig",
                }
    except Exception:
        pass

def get_all_bluetooth_adapters(auto_power_on: bool = True, timeout_s: float = 3.0) -> list:
    """Discover all active/available Bluetooth adapters on the system.

    Returns a list of dicts with:
      'mac': Controller MAC address (e.g. '8C:68:8B:C2:E6:FA')
      'hci': Interface name (e.g. 'hci0', 'hci1')
      'name': Friendly device/alias name
      'powered': Boolean power status
      'is_default': Boolean indicating default adapter
    """
    unblock_rfkill()
    adapters = {}

    # 1. Query hcitool dev for HCI -> MAC mapping
    try:
        out = subprocess.check_output(["hcitool", "dev"], text=True, stderr=subprocess.DEVNULL, timeout=timeout_s)
        for line in out.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                hci, mac = parts[0], parts[1].upper()
                adapters[mac] = {
                    "mac": mac,
                    "hci": hci,
                    "name": hci,
                    "powered": None,
                    "is_default": False,
                }
    except Exception:
        pass

    # 2. Query hciconfig for UP/RUNNING state
    try:
        out = subprocess.check_output(["hciconfig"], text=True, stderr=subprocess.DEVNULL, timeout=timeout_s)
        for block in out.split("\n\n"):
            dev_m = re.search(r"^(hci\d+):", block, re.MULTILINE)
            mac_m = re.search(r"BD Address:\s*([0-9A-Fa-f:]{17})", block)
            if mac_m:
                mac = mac_m.group(1).upper()
                hci = dev_m.group(1) if dev_m else "hci"
                is_up = "UP RUNNING" in block or "UP" in block
                if mac not in adapters:
                    adapters[mac] = {
                        "mac": mac,
                        "hci": hci,
                        "name": hci,
                        "powered": is_up,
                        "is_default": False,
                    }
                else:
                    adapters[mac]["hci"] = hci
                    adapters[mac]["powered"] = is_up
    except Exception:
        pass

    # 3. Query bluetoothctl list
    try:
        out = subprocess.check_output(["bluetoothctl", "list"], text=True, stderr=subprocess.DEVNULL, timeout=timeout_s)
        for line in out.strip().splitlines():
            m = re.search(r"Controller\s+([0-9A-Fa-f:]{17})\s*(.*)", line)
            if m:
                mac = m.group(1).upper()
                rest = m.group(2).strip()
                is_default = "[default]" in rest
                name = rest.replace("[default]", "").strip()
                if mac not in adapters:
                    adapters[mac] = {
                        "mac": mac,
                        "hci": "hci",
                        "name": name or "Bluetooth Adapter",
                        "powered": None,
                        "is_default": is_default,
                    }
                else:
                    adapters[mac]["name"] = name or adapters[mac]["name"]
                    adapters[mac]["is_default"] = is_default
    except Exception:
        pass

    # 4. Check and ensure power on all detected adapters
    any_powered_on = False
    for mac, info in adapters.items():
        try:
            show = subprocess.check_output(["bluetoothctl", "show", mac], text=True, stderr=subprocess.DEVNULL, timeout=timeout_s)
            p_m = re.search(r"Powered:\s+(yes|no)", show, re.IGNORECASE)
            if p_m:
                info["powered"] = (p_m.group(1).lower() == "yes")
        except Exception:
            pass

        if auto_power_on and not info.get("powered"):
            power_on_adapter(mac, timeout_s=timeout_s)
            info["powered"] = True
            any_powered_on = True

    if any_powered_on:
        time.sleep(1.0)
        # Re-query without auto_power_on so re-enumerated interfaces (e.g. hci0 -> hci2) are accurately mapped
        return get_all_bluetooth_adapters(auto_power_on=False, timeout_s=timeout_s)

    adapter_list = list(adapters.values())
    # Sort default adapter first
    adapter_list.sort(key=lambda a: (not a.get("is_default", False), a.get("hci", "")))
    return adapter_list


def ensure_bluetooth_ready(auto_power_on: bool = True, verbose: bool = False, return_all: bool = False) -> Any:
    """Ensure Bluetooth is ready, unblocked, powered on, and return adapter details.

    If return_all is True, returns a list of all detected, active adapter dictionaries.
    If return_all is False, returns the primary/default adapter dictionary.

    Raises:
        BluetoothAdapterError: If no adapter is found or powered on.
    """
    # 0. Ensure Warble FD_SETSIZE patch is in place
    try:
        from scripts.patch_warble import ensure_warble_patched
        ensure_warble_patched(verbose=False)
    except Exception:
        pass

    # 1. Unblock rfkill
    unblock_rfkill()

    # 2. Check service status
    service_ok = is_service_active()
    if not service_ok and verbose:
        print("[Bluetooth Warning] 'bluetooth' system service is not reporting 'active'.")

    # 3. Query all controllers
    adapters = get_all_bluetooth_adapters(auto_power_on=auto_power_on)

    # Retry once after short delay if empty (in case dongle was freshly inserted)
    if not adapters:
        time.sleep(0.5)
        adapters = get_all_bluetooth_adapters(auto_power_on=auto_power_on)

    if not adapters:
        raise BluetoothAdapterError(
            "\n"
            "========================================================================\n"
            "  [ERROR] NO BLUETOOTH ADAPTER DETECTED!\n"
            "========================================================================\n"
            "  The MetaWear pipeline requires an active Bluetooth LE controller.\n"
            "  Causes & Solutions:\n"
            "  1. USB Bluetooth antenna is unplugged:\n"
            "     -> Plug your USB Bluetooth antenna firmly into a USB port on this laptop.\n"
            "  2. USB device not recognized:\n"
            "     -> Check `lsusb` to confirm Realtek/Bluetooth device is listed.\n"
            "  3. Bluetooth service stopped:\n"
            "     -> Run: sudo systemctl restart bluetooth\n"
            "========================================================================"
        )

    if return_all:
        return adapters
    return adapters[0]

