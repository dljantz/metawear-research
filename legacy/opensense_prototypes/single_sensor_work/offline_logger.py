import sys
from time import sleep
from threading import Event
from mbientlab.metawear import MetaWear, libmetawear, parse_value, create_voidp
from mbientlab.metawear.cbindings import *

# 1. Replace this with your board's MAC address
MAC_ADDRESS = "D8:FB:07:F7:24:50" 
#MAC_ADDRESS = "CC:DD:59:8A:67:7A"

print(f"Connecting to {MAC_ADDRESS}...")
device = MetaWear(MAC_ADDRESS) # create MetaWear Python object to manipulate
device.connect() # establish 2-way connection
print("Connected successfully!")

# 2. Configure the Accelerometer
print("Configuring accelerometer...")
libmetawear.mbl_mw_acc_set_odr(device.board, 50.0) # 50Hz data rate
libmetawear.mbl_mw_acc_set_range(device.board, 4.0) # +/- 4G range (up to 4x Earth's gravity in any direction)
libmetawear.mbl_mw_acc_write_acceleration_config(device.board) # apply above settings to the board

# 3. Setup the Logger
print("Setting up logger...")
signal = libmetawear.mbl_mw_acc_get_acceleration_data_signal(device.board) # create reference to raw data stream
# below -- command MetaMotion to store its accelerometer data locally. wait for success, then make C++ pointer to that logger
logger = create_voidp(lambda fn: libmetawear.mbl_mw_datasignal_log(signal, None, fn), resource = "acc_logger")

# 4. Start Logging and Sensor Sampling
libmetawear.mbl_mw_logging_start(device.board, 0)
libmetawear.mbl_mw_acc_enable_acceleration_sampling(device.board)
libmetawear.mbl_mw_acc_start(device.board)

print("\n--------------------------------------------------")
print("Logging started!")
print("The board is now recording data to its internal memory.")
print("You can disconnect it from USB, walk out of Bluetooth range, and move it around.")
print("--------------------------------------------------\n")

# Pause the script until you are ready to download
input("Press [ENTER] when you have returned the board to download the data...")

# 5. Stop the Sensor and Logging
print("\nStopping sensor and logging...")
libmetawear.mbl_mw_acc_stop(device.board)
libmetawear.mbl_mw_acc_disable_acceleration_sampling(device.board)
libmetawear.mbl_mw_logging_stop(device.board)

# 6. Setup the Download Handlers
print("Downloading data...")
e = Event() # set up Event flag to pause script until data transfer completes

# this function flips Event flag to true when all data has been downloaded
def progress_update_handler(context, entries_left, total_entries):
    if (entries_left == 0):
        e.set()

fn_wrapper = FnVoid_VoidP_UInt_UInt(progress_update_handler) # cast the function we just made into a strict C-function pointer

# below: create a configuration object to handle incoming data. second line passes in the C pointer we just made, last two lines just cast corrupted data to None so they get ignored
download_handler = LogDownloadHandler(
    context = None, 
    received_progress_update = fn_wrapper, 
    received_unknown_entry = cast(None, FnVoid_VoidP_UByte_Long_UByteP_UByte), 
    received_unhandled_entry = cast(None, FnVoid_VoidP_DataP)
)

# This callback fires for every single data point downloaded
callback = FnVoid_VoidP_DataP(lambda ctx, p: print(f"Epoch: {p.contents.epoch}, Value: {parse_value(p)}"))
libmetawear.mbl_mw_logger_subscribe(logger, None, callback) # subscribe to the place in the board's memory where data was stored and say to trigger "callback" when each data point gets downloaded

# 7. Trigger the Download
libmetawear.mbl_mw_logging_download(device.board, 0, byref(download_handler))

# Wait for the download to finish
e.wait()

print("\nDownload complete!")

# 8. Clean up and Disconnect
print("Erasing log memory and disconnecting...")
libmetawear.mbl_mw_logger_remove(logger)
libmetawear.mbl_mw_debug_reset(device.board) 
device.disconnect()
print("Done.")
