import sys
from time import sleep
from threading import Event
import pandas as pd
from mbientlab.metawear import MetaWear, libmetawear, parse_value, create_voidp
from mbientlab.metawear.cbindings import *

# 1. Define your sensor network
SENSORS = {
    "torso_imu": "D8:FB:07:F7:24:50", # Your original sensor
    "femur_r_imu": "CC:DD:59:8A:67:7A"  # Your second sensor
}

OUTPUT_FILE = "opensense_multi_orientations.sto"

devices = {}
loggers = {}
sensor_data = {name: [] for name in SENSORS.keys()}

print("--- CONNECTING ---")
for name, mac in SENSORS.items():
    print(f"Connecting to {name} ({mac})...")
    d = MetaWear(mac)
    d.connect()
    devices[name] = d
    print(f"{name} connected!")

print("\n--- CONFIGURING ---")
for name, d in devices.items():
    libmetawear.mbl_mw_sensor_fusion_set_mode(d.board, SensorFusionMode.NDOF)
    libmetawear.mbl_mw_sensor_fusion_write_config(d.board)
    
    signal = libmetawear.mbl_mw_sensor_fusion_get_data_signal(d.board, SensorFusionData.QUATERNION)
    # We create a unique logger resource string for each device
    loggers[name] = create_voidp(lambda fn: libmetawear.mbl_mw_datasignal_log(signal, None, fn), resource=f"{name}_logger")

print("\n--- STARTING SENSORS ---")
# Start them as close together as Python allows
for name, d in devices.items():
    libmetawear.mbl_mw_logging_start(d.board, 0)
    libmetawear.mbl_mw_sensor_fusion_enable_data(d.board, SensorFusionData.QUATERNION)
    libmetawear.mbl_mw_sensor_fusion_start(d.board)

print("\n--------------------------------------------------")
print("LOGGING STARTED FOR ALL SENSORS!")
print("Perform a sharp sync gesture (like a small jump or heel strike), then move.")
print("--------------------------------------------------\n")

input("Press [ENTER] to stop and download data...")

print("\n--- STOPPING SENSORS ---")
for name, d in devices.items():
    libmetawear.mbl_mw_sensor_fusion_stop(d.board)
    libmetawear.mbl_mw_sensor_fusion_clear_enabled_mask(d.board)
    libmetawear.mbl_mw_logging_stop(d.board)

print("\n--- DOWNLOADING DATA ---")
download_events = {name: Event() for name in SENSORS.keys()}

# A factory function to create unique callbacks for each sensor
def make_data_callback(sensor_name):
    def callback(ctx, p):
        quat = parse_value(p)
        sensor_data[sensor_name].append({
            'epoch': p.contents.epoch,
            sensor_name: f"{quat.w},{quat.x},{quat.y},{quat.z}"
        })
    return FnVoid_VoidP_DataP(callback)

def make_progress_callback(sensor_name):
    def callback(context, entries_left, total_entries):
        if entries_left == 0:
            download_events[sensor_name].set()
    return FnVoid_VoidP_UInt_UInt(callback)

callbacks = [] # Keep references alive so C++ doesn't garbage collect them

for name, d in devices.items():
    print(f"Triggering download for {name}...")
    
    prog_cb = make_progress_callback(name)
    callbacks.append(prog_cb) # Prevent garbage collection
    
    download_handler = LogDownloadHandler(
        context = None, 
        received_progress_update = prog_cb, 
        received_unknown_entry = cast(None, FnVoid_VoidP_UByte_Long_UByteP_UByte), 
        received_unhandled_entry = cast(None, FnVoid_VoidP_DataP)
    )
    
    data_cb = make_data_callback(name)
    callbacks.append(data_cb) # Prevent garbage collection
    libmetawear.mbl_mw_logger_subscribe(loggers[name], None, data_cb)
    
    libmetawear.mbl_mw_logging_download(d.board, 0, byref(download_handler))

# Wait for ALL sensors to finish downloading
for name, e in download_events.items():
    e.wait()
    print(f"{name} download complete!")

print("\n--- ALIGNING DATA WITH PANDAS ---")
# 1. Find the absolute earliest timestamp across all sensors
global_start_epoch = min([min([row['epoch'] for row in sensor_data[name]]) for name in SENSORS.keys()])

dfs = []
for name in SENSORS.keys():
    # Convert list of dicts to DataFrame
    df = pd.DataFrame(sensor_data[name])
    
    # Normalize time to start at 0.00 seconds
    df['time'] = (df['epoch'] - global_start_epoch) / 1000.0
    
    # Round to nearest 0.01s (100Hz) to align the mismatched clocks
    df['time'] = df['time'].round(2)
    
    # Drop duplicates in case two epochs rounded to the same hundredth
    df = df.drop_duplicates(subset=['time']).drop(columns=['epoch'])
    dfs.append(df)

# Merge all sensor DataFrames on the unified 'time' column
merged_df = dfs[0]
for i in range(1, len(dfs)):
    merged_df = pd.merge(merged_df, dfs[i], on='time', how='outer')

# Sort chronologically and fill in any tiny gaps caused by clock drift
merged_df = merged_df.sort_values('time')
merged_df.ffill(inplace=True) 
merged_df.bfill(inplace=True)

print("\n--- WRITING OPENSENSE FILE ---")
with open(OUTPUT_FILE, "w") as f:
    f.write("DataRate=100.000000\n") 
    f.write("DataType=Quaternion\n")
    f.write("version=3\n")
    f.write("OpenSimVersion=4.5\n")
    f.write("endheader\n")
    
    # Write column headers dynamically based on your dictionary
    sensor_names = list(SENSORS.keys())
    f.write("time\t" + "\t".join(sensor_names) + "\n")
    
    # Write the data rows
    for index, row in merged_df.iterrows():
        f.write(f"{row['time']:.2f}")
        for name in sensor_names:
            f.write(f"\t{row[name]}")
        f.write("\n")

print(f"Data saved to {OUTPUT_FILE}")

print("\n--- CLEANUP ---")
for name, d in devices.items():
    libmetawear.mbl_mw_logger_remove(loggers[name])
    d.disconnect()
print("Done!")
