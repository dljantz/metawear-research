import sys
from pathlib import Path
from mbientlab.metawear import MetaWear
from mbientlab.metawear.cbindings import *
from mbientlab.warble import * 
from time import sleep

import platform
import six

# Add project root for bluetooth_utils import
for parent in Path(__file__).resolve().parents:
    if (parent / "pipeline").exists():
        sys.path.insert(0, str(parent))
        break

from pipeline.core.bluetooth_utils import ensure_bluetooth_ready

# Ensure Bluetooth adapter is unblocked and powered on (blue LED on)
adapter_info = ensure_bluetooth_ready(auto_power_on=True)
HCI_MAC = adapter_info["mac"]
print(f"Active Bluetooth Adapter: {HCI_MAC} (Blue LED ON)")

selection = -1
devices = None

while selection == -1:
    print("scanning for devices...")
    devices = {}
    def handler(result):
        devices[result.mac] = result.name

    BleScanner.set_handler(handler)
    BleScanner.start(hci=HCI_MAC)

    sleep(10.0)
    BleScanner.stop()

    i = 0
    for address, name in six.iteritems(devices):
        print("[%d] %s (%s)" % (i, address, name))
        i += 1

    msg = "Select your device (-1 to rescan): "
    selection = int(raw_input(msg) if platform.python_version_tuple()[0] == '2' else input(msg))

print("Waiting 5 seconds while BLE hardware switches from scan to connect mode")
sleep(5)
address = list(devices)[selection]
print("Connecting to %s..." % (address))
device = MetaWear(address, hci_mac=HCI_MAC)
device.connect()

print("Connected to " + device.address + " over " + ("USB" if device.usb.is_connected else "BLE"))
print("Device information: " + str(device.info))
sleep(5.0)

device.disconnect()
sleep(1.0)
print("Disconnected") 
