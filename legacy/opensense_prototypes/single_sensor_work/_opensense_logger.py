import sys
from time import sleep
from threading import Event
from mbientlab.metawear import MetaWear, libmetawear, parse_value, create_voidp
from mbientlab.metawear.cbindings import *

MAC_ADDRESS = "D8:FB:07:F7:24:50" 
OUTPUT_FILE = "opensense_orientations.sto"

print(f"Connecting to {MAC_ADDRESS}...")
device = MetaWear(MAC_ADDRESS)
device.connect()
print("Connected successfully!")

# 1. Configure Sensor Fusion (NDoF Mode)
print("Configuring sensor fusion (NDoF mode)...")
libmetawear.mbl_mw_sensor_fusion_set_mode(device.board, SensorFusionMode.NDOF)
libmetawear.mbl_mw_sensor_fusion_write_config(device.board)

# 2. Setup the Logger for Quaternions
print("Setting up logger...")
signal = libmetawear.mbl_mw_sensor_fusion_get_data_signal(device.board, SensorFusionData.QUATERNION)
logger = create_voidp(lambda fn: libmetawear.mbl_mw_datasignal_log(signal, None, fn), resource = "quat_logger")

# 3. Start Logging and Sensor Fusion
libmetawear.mbl_mw_logging_start(device.board, 0)
libmetawear.mbl_mw_sensor_fusion_enable_data(device.board, SensorFusionData.QUATERNION)
libmetawear.mbl_mw_sensor_fusion_start(device.board)

print("\n--------------------------------------------------")
print("LOGGING STARTED (QUATERNIONS)!")
print("The board is now recording 100Hz absolute orientation data.")
print("Walk around, rotate the board in all axes.")
print("--------------------------------------------------\n")

input("Press [ENTER] when you have returned the board to download the data...")

# 4. Stop Sensor Fusion and Logging
print("\nStopping sensor fusion and logging...")
libmetawear.mbl_mw_sensor_fusion_stop(device.board)
libmetawear.mbl_mw_sensor_fusion_clear_enabled_mask(device.board)
libmetawear.mbl_mw_logging_stop(device.board)

# 5. Open the .sto file and write the OpenSense Header
print(f"Opening {OUTPUT_FILE} for writing...")
f = open(OUTPUT_FILE, "w")
f.write("DataRate=100.000000\n") 
f.write("DataType=Quaternion\n")
f.write("version=3\n")
f.write("OpenSimVersion=4.5\n")
f.write("endheader\n")
f.write("time\ttorso_imu\n") # Naming our virtual column 'torso_imu'

# 6. Setup the Download Handlers
print("Downloading data...")
e = Event()
first_epoch = None

def progress_update_handler(context, entries_left, total_entries):
    if (entries_left == 0):
        e.set()

fn_wrapper = FnVoid_VoidP_UInt_UInt(progress_update_handler)

download_handler = LogDownloadHandler(
    context = None, 
    received_progress_update = fn_wrapper, 
    received_unknown_entry = cast(None, FnVoid_VoidP_UByte_Long_UByteP_UByte), 
    received_unhandled_entry = cast(None, FnVoid_VoidP_DataP)
)

# 7. Format and write the data points
data_counter = 1
def data_callback(ctx, p):
    global first_epoch
    global data_counter

    # Extract the raw epoch timestamp
    epoch = p.contents.epoch
    if first_epoch is None:
        first_epoch = epoch
    
    # Normalize time to start at 0.00 seconds
    time_sec = (epoch - first_epoch) / 1000.0
    
    # parse_value returns an object with w, x, y, z attributes
    quat = parse_value(p)
    
    # Write the formatted row to the text file
    f.write(f"{time_sec:.3f}\t{quat.w},{quat.x},{quat.y},{quat.z}\n")
    
    # just a bit of print statement debugging :)
    print(time_sec)

callback = FnVoid_VoidP_DataP(data_callback)
libmetawear.mbl_mw_logger_subscribe(logger, None, callback)

# 8. Trigger the Download
libmetawear.mbl_mw_logging_download(device.board, 0, byref(download_handler))

e.wait()
f.close()

print(f"\nDownload complete! Formatted data saved to: {OUTPUT_FILE}")

# 9. Clean up and Disconnect
print("Erasing log memory and disconnecting...")
libmetawear.mbl_mw_logger_remove(logger)
#libmetawear.mbl_mw_debug_reset(device.board) 

device.disconnect()
print("Done.")
