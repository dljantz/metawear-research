import sys
from pathlib import Path
from time import sleep

# Add project root for bluetooth_utils import
for parent in Path(__file__).resolve().parents:
    if (parent / "pipeline").exists():
        sys.path.insert(0, str(parent))
        break

from pipeline.core.bluetooth_utils import ensure_bluetooth_ready
from mbientlab.metawear import MetaWear

if len(sys.argv) < 2:
    print("Error: Please provide a MAC address.")
    print("Usage: python3 direct_connect.py <MAC_ADDRESS>")
    sys.exit(1)

# Ensure Bluetooth adapter is unblocked and powered on (blue LED on)
adapter_info = ensure_bluetooth_ready(auto_power_on=True)
HCI_MAC = adapter_info["mac"]
print(f"Active Bluetooth Adapter: {HCI_MAC} (Blue LED ON)")

address = sys.argv[1]
print("Connecting directly to %s..." % (address))

# Instantiate the device with the hardcoded MAC address and active adapter
device = MetaWear(address, hci_mac=HCI_MAC)

try:
    # Connect directly without scanning first
    device.connect()
    
    print("Connected to " + device.address + " over " + ("USB" if device.usb.is_connected else "BLE"))
    print("Device information: " + str(device.info))
    
    # Keep connection open for 5 seconds
    sleep(5.0)

except Exception as e:
    print("Connection failed: ", e)

finally:
    device.disconnect()
    sleep(1.0)
    print("Disconnected")
