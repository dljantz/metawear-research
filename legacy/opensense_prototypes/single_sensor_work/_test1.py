from mbientlab.metawear.cbindings import * # allows comm with underlying C++ library
from mbientlab.warble import * # Warble is a custom BLE library
from mbientlab.metawear import * # core metawear functionality
from threading import Event
import time

e = Event() # internal flag that communicates between main script and background bluetooth scanning process
address = None # to eventually hold MAC address of the sensor
def device_discover_task(result): # callback function -- executes every time a nearby bluetooth device is detected
    global address # not making a new address variable, keep the global one
    if (result.has_service_uuid(MetaWear.GATT_SERVICE)): # check to see if the pinging device is sending MetaWear-specific pings
        # grab the first discovered metawear device
        address = result.mac
        e.set() # change Event flag from False to True

BleScanner.set_handler(device_discover_task) # hand newly created callback function to Warble -- tell it to evaluate every device it sees
BleScanner.start() # start scanning for bluetooth devices
e.wait() # wait indefinitely until background scanner triggers e.set()
print("grabbed first discovered metawear device with address " + address)
device = MetaWear(address) # create MetaWear object
#print("made it to point A")
try:
    device.connect()
except WarbleException as e:
    print(f"Connection failed: {e}")
    device.disconnect()  # CRITICAL: This releases the file descriptor back to the OS

# blink the LED green forever -- taken from led.py
pattern= LedPattern(repeat_count= Const.LED_REPEAT_INDEFINITELY)
libmetawear.mbl_mw_led_load_preset_pattern(byref(pattern), LedPreset.BLINK)
libmetawear.mbl_mw_led_write_pattern(device.board, byref(pattern), LedColor.GREEN)
libmetawear.mbl_mw_led_play(device.board)
time.sleep(10)
libmetawear.mbl_mw_led_stop_and_clear(device.board)
time.sleep(1)

BleScanner.stop()
print("_test1.py terminated successfully")

