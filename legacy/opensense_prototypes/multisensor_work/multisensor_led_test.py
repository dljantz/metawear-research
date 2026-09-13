from mbientlab.metawear.cbindings import * # allows comm with underlying C++ library
from mbientlab.warble import * # Warble is a custom BLE library
from mbientlab.metawear import * # core metawear functionality
from threading import Event
import time

e = Event() # internal flag that communicates between main script and background bluetooth scanning process
addresses = ["D8:FB:07:F7:24:50", "F3:A3:7B:95:51:CD"]

#BleScanner.start() # start scanning for bluetooth devices
#e.wait() # wait indefinitely until background scanner triggers e.set()
#print("grabbed first discovered metawear device with address " + address)
devices = [MetaWear(addresses[i]) for i in range(len(addresses))]
#print("made it to point A")
print(devices)
try:
    for i in range(len(devices)):
        devices[i].connect()
        print("Device " + str(i) + " connected.")
        time.sleep(2)  # Buffer to let the BLE stack settle

except WarbleException as e:
    print(f"Connection failed: {e}")
    for device in devices:
        device.disconnect()  # CRITICAL: This releases the file descriptor back to the OS

# blink the LED green forever -- taken from led.py
pattern= LedPattern(repeat_count= Const.LED_REPEAT_INDEFINITELY)
libmetawear.mbl_mw_led_load_preset_pattern(byref(pattern), LedPreset.BLINK)

for device in devices:
    libmetawear.mbl_mw_led_write_pattern(device.board, byref(pattern), LedColor.GREEN)

for device in devices:
    libmetawear.mbl_mw_led_play(device.board)

time.sleep(10)

for device in devices:
    libmetawear.mbl_mw_led_stop_and_clear(device.board)
    
time.sleep(1)

for device in devices:
    device.disconnect()

print("multisensor_led_test.py terminated successfully")

