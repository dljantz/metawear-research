"""
The purpose of this script is to wipe preexisting data from the Metawear
board. Historically there have been weird issues with previous data
collection events corrupting current ones... it's a headache that can be 
avoided by running this script between data collection runs.
"""

import sys
from pathlib import Path
from time import sleep

# Add project root for bluetooth_utils import
for parent in Path(__file__).resolve().parents:
    if (parent / "pipeline").exists():
        sys.path.insert(0, str(parent))
        break

from pipeline.core.bluetooth_utils import ensure_bluetooth_ready
from mbientlab.metawear import MetaWear, libmetawear

# Ensure Bluetooth adapter is unblocked and powered on (blue LED on)
adapter_info = ensure_bluetooth_ready(auto_power_on=True)
HCI_MAC = adapter_info["mac"]
print(f"Active Bluetooth Adapter: {HCI_MAC} (Blue LED ON)")

# Replace with your actual MAC
#MAC_ADDRESS = "D8:FB:07:F7:24:50" 
MAC_ADDRESS = "CC:DD:59:8A:67:7A"

print(f"Connecting to {MAC_ADDRESS} to wipe...")
device = MetaWear(MAC_ADDRESS, hci_mac=HCI_MAC)
device.connect()

print("Stopping any active logging...")
libmetawear.mbl_mw_logging_stop(device.board)

print("Clearing flash memory...")
libmetawear.mbl_mw_logging_clear_entries(device.board)

# Wait for the physical flash erase
sleep(3.0)

print("Tearing down board state...")
libmetawear.mbl_mw_metawearboard_tear_down(device.board) 

print("Sending hard reset...")
libmetawear.mbl_mw_debug_reset(device.board)

# We expect a disconnect error here because the board reboots,
# 	so we use a try/except
try:
    device.disconnect()
except:
    pass

print("Wipe complete. The board is resetting.")
