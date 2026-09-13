"""
The purpose of this script is to wipe preexisting data from the Metawear
board. Historically there have been weird issues with previous data
collection events corrupting current ones... it's a headache that can be 
avoided by running this script between data collection runs.
"""

import sys
from time import sleep
from mbientlab.metawear import MetaWear, libmetawear

# printed on backs of sensors
addresses = ["D8:FB:07:F7:24:50", "F3:A3:7B:95:51:CD"]
devices = []

for address in addresses:
    print(f"Connecting to {address} to wipe...")
    devices.append(MetaWear(address)) # Calling the MetaWear constructor returns a MetaWear object, append to devices list
    devices[-1].connect()

for device in devices:
    print("Stopping any active logging...")
    libmetawear.mbl_mw_logging_stop(device.board)

for device in devices:
    print("Clearing flash memory...")
    libmetawear.mbl_mw_logging_clear_entries(device.board)

print("Waiting for the physical flash erase...")
sleep(3.0)

print("Tearing down board state...")
for device in devices:
    libmetawear.mbl_mw_metawearboard_tear_down(device.board) 

for device in devices:
    print("Sending hard reset...")
    libmetawear.mbl_mw_debug_reset(device.board)

# We expect a disconnect error here because the board reboots,
# 	so we use a try/except
for device in devices:
    try:
        device.disconnect()
    except:
        pass

print("Wipe complete. The boards are resetting.")
