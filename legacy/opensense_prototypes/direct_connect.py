# usage: sudo python3 direct_connect.py F3:A3:7B:95:51:CD
import sys
from mbientlab.metawear import MetaWear
from time import sleep

if len(sys.argv) < 2:
    print("Error: Please provide a MAC address.")
    print("Usage: sudo python3 direct_connect.py <MAC_ADDRESS>")
    sys.exit(1)

address = sys.argv[1]
print("Connecting directly to %s..." % (address))

# Instantiate the device with the hardcoded MAC address
device = MetaWear(address)

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
