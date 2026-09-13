from mbientlab.metawear.cbindings import * # allows comm with underlying C++ library
from mbientlab.warble import * # Warble is a custom BLE library
from mbientlab.metawear import * # core metawear functionality
from threading import Event
import time

e = Event() # internal flag that communicates between main script and background bluetooth scanning process
address0 = "D8:FB:07:F7:24:50"
address1 = "F3:A3:7B:95:51:CD"

#BleScanner.start() # start scanning for bluetooth devices
#e.wait() # wait indefinitely until background scanner triggers e.set()
#print("grabbed first discovered metawear device with address " + address)
device0 = MetaWear(address0) # create MetaWear object
device1 = MetaWear(address1)

#print("made it to point A")
try:
    device0.connect()
    print("Device 0 connected.")
    time.sleep(2) # Buffer to let the BLE stack settle
    
    device1.connect()
    print("Device 1 connected.")
    time.sleep(2)

except WarbleException as e:
    print(f"Connection failed: {e}")
    device0.disconnect()  # CRITICAL: This releases the file descriptor back to the OS
    device1.disconnect()

# blink the LED green forever -- taken from led.py
pattern= LedPattern(repeat_count= Const.LED_REPEAT_INDEFINITELY)
libmetawear.mbl_mw_led_load_preset_pattern(byref(pattern), LedPreset.BLINK)
libmetawear.mbl_mw_led_write_pattern(device0.board, byref(pattern), LedColor.GREEN)
libmetawear.mbl_mw_led_write_pattern(device1.board, byref(pattern), LedColor.GREEN)
libmetawear.mbl_mw_led_play(device0.board)
libmetawear.mbl_mw_led_play(device1.board)
time.sleep(10)
libmetawear.mbl_mw_led_stop_and_clear(device0.board)
libmetawear.mbl_mw_led_stop_and_clear(device1.board)
time.sleep(1)

#BleScanner.stop()
device0.disconnect()
device1.disconnect()

print("two_sensor_led_test.py terminated successfully")

