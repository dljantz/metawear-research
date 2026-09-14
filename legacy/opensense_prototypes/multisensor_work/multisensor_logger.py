import sys
from pathlib import Path
import time
import csv
from threading import Event

# Add project root for bluetooth_utils import
for parent in Path(__file__).resolve().parents:
    if (parent / "pipeline").exists():
        sys.path.insert(0, str(parent))
        break

from pipeline.core.bluetooth_utils import ensure_bluetooth_ready
from mbientlab.metawear import MetaWear, libmetawear, parse_value
from mbientlab.metawear.cbindings import *

# Ensure Bluetooth adapter is unblocked and powered on (blue LED on)
adapter_info = ensure_bluetooth_ready(auto_power_on=True)
HCI_MAC = adapter_info["mac"]
print(f"Active Bluetooth Adapter: {HCI_MAC} (Blue LED ON)")

# --- 1. CONFIGURATION ---
TRIAL_DURATION_S = 30.0

# direct MAC assignment bypasses BleScanner.start() method, which is finicky.
devices_config = [
    {"name": "torso_imu", "mac": "D8:FB:07:F7:24:50"}, 
    {"name": "femur_r_imu", "mac": "F3:A3:7B:95:51:CD"}
]

states = [] # holds list of sensors that we were able to connect to.
callbacks = [] # CRITICAL: Prevents C-callbacks from being garbage collected

# the point here is to be a container for each sensor.
class DeviceState:
    def __init__(self, config):
        self.name = config["name"]
        self.device = MetaWear(config["mac"], hci_mac=HCI_MAC)
        self.loggers = []
        self.quat_data = []
        self.accel_data = []
        self.download_event = Event()
        self.setup_event = Event()
        self.last_print_time = 0.0  # tracks progress during logging download
	
# --- 2. CONNECT TO DEVICES ---
print("Connecting to sensors (bypassing scanner)...")
for config in devices_config:
    state = DeviceState(config) # create the homemade sensor Object
    try:
        state.device.connect()
        print(f"Connected to {state.name} ({state.device.address})")
        states.append(state)
        time.sleep(2.0) # Our stable 2.0s buffer to prevent OS BlueZ crashes
    except Exception as e:
        print(f"Failed to connect to {state.name}: {e}")
        sys.exit(1)
        
print("\nClearing ghost loggers from previous runs...")
for state in states:
    board = state.device.board
    libmetawear.mbl_mw_logging_stop(board)
    libmetawear.mbl_mw_logging_clear_entries(board)
    time.sleep(1.0)  # Give it a second to erase

# --- 3. CONFIGURE NDOF SENSOR FUSION & LOGGING ---
print("\nConfiguring 100Hz NDoF Quaternion logging...")
for state in states:
    board = state.device.board

    # We ONLY use sensor fusion for opensense. However, we do collect
    # 	raw acceleration data to conduct linear time interpolation on 
    # 	the data -- this "squeezes" or "stretches" the data to account
    # 	clock drift and is all synchronized by a jolt at the beginning
    # 	and the end. Hence accelerometer configuration below:

    # Set ODR to 100Hz and Range to 16G for impact detection
    libmetawear.mbl_mw_acc_set_odr(board, 100.0)
    libmetawear.mbl_mw_acc_set_range(board, 16.0)
    libmetawear.mbl_mw_acc_write_acceleration_config(board)

    # Configure NDoF mode (automatically sets to 100Hz)
    libmetawear.mbl_mw_sensor_fusion_set_mode(board, SensorFusionMode.NDOF)
    libmetawear.mbl_mw_sensor_fusion_write_config(board)
    
    # Get the Quaternion data signal (and the acclerometer signal for data smooshing)
    quat_signal = libmetawear.mbl_mw_sensor_fusion_get_data_signal(board, SensorFusionData.QUATERNION)
    accel_signal = libmetawear.mbl_mw_acc_get_acceleration_data_signal(board)

    # Create logger for this signal
    def logger_created(context, pointer):
        if pointer:
            state.loggers.append(pointer)
        else:
            print(f"\nCRITICAL ERROR: Failed to create logger for {state.name} because boards have ghost loggers in memory.")
        state.setup_event.set()

    logger_callback = FnVoid_VoidP_VoidP(logger_created)
    callbacks.append(logger_callback) # Keep in memory
    
    # Log Quaternion and wait
    state.setup_event.clear()
    libmetawear.mbl_mw_datasignal_log(quat_signal, None, logger_callback)
    state.setup_event.wait()

    # Log Accelerometer and wait
    state.setup_event.clear()
    libmetawear.mbl_mw_datasignal_log(accel_signal, None, logger_callback)
    state.setup_event.wait()
    
    # Start the logger and sensor fusion algorithm
    libmetawear.mbl_mw_logging_start(board, 0)
    libmetawear.mbl_mw_sensor_fusion_enable_data(board, SensorFusionData.QUATERNION)
    libmetawear.mbl_mw_sensor_fusion_start(board)

    # Start the accelerometer
    libmetawear.mbl_mw_acc_enable_acceleration_sampling(board)
    libmetawear.mbl_mw_acc_start(board)


# --- 4. EXECUTE TRIAL ---
print(f"\n*** LOGGING STARTED FOR {TRIAL_DURATION_S} SECONDS ***")
print("--> Please perform your physical Sync Gesture (e.g., sharp jump/stomp) NOW <--")
time.sleep(TRIAL_DURATION_S)
print("--> Stand perfectly still for a second to reestablish resting baseline <--")
time.sleep(1)
print("--> Perform second physical sync gesture in the next three seconds. <--")
time.sleep(3)
print("*** LOGGING COMPLETE ***\n")

# --- 5. STOP LOGGING ---
for state in states:
    board = state.device.board
    
    libmetawear.mbl_mw_acc_stop(board)
    libmetawear.mbl_mw_acc_disable_acceleration_sampling(board)

    # Stop fusion and logging
    libmetawear.mbl_mw_sensor_fusion_stop(board)
    #libmetawear.mbl_mw_sensor_fusion_clear_enabled_data(board)
    libmetawear.mbl_mw_logging_stop(board)

# --- 6. DOWNLOAD DATA ---
for state in states:
    board = state.device.board

    # CRITICAL FOR METAMOTIONS: Flush the NAND flash memory page
    libmetawear.mbl_mw_logging_flush_page(board)
    time.sleep(1.0) 

    print(f"Downloading data from {state.name}...")
    
    # Setup Download Handlers
    def progress_update_handler(context, entries_left, total_entries):
        if total_entries > 0:
            current_time = time.time()
            
            if current_time - state.last_print_time >= 5.0 or entries_left == 0:
                percent = ((total_entries - entries_left) / total_entries) * 100
                print(f"[{state.name}] Download progress: {percent:.1f}% ({total_entries - entries_left}/{total_entries} entries")
                state.last_print_time = current_time
                
        if entries_left == 0:
            state.download_event.set()

    def data_handler(context, p):
        val = parse_value(p)
        # Use pythonic attribute checking to distinguish C-structs
        if hasattr(val, 'w'):
            state.quat_data.append([p.contents.epoch, val.w, val.x, val.y, val.z])
        elif hasattr(val, 'x'):
            state.accel_data.append([p.contents.epoch, val.x, val.y, val.z])

    fn_progress = FnVoid_VoidP_UInt_UInt(progress_update_handler)
    fn_data = FnVoid_VoidP_DataP(data_handler)
    callbacks.extend([fn_progress, fn_data]) # Keep in memory
    
    download_handler = LogDownloadHandler(
        context=None,
        received_progress_update=fn_progress,
        received_unknown_entry=cast(None, FnVoid_VoidP_UByte_Long_UByteP_UByte),
        received_unhandled_entry=cast(None, FnVoid_VoidP_DataP)
    )

    # Subscribe to the logger output and trigger download
    for logger in state.loggers:
        libmetawear.mbl_mw_logger_subscribe(logger, None, fn_data)
    libmetawear.mbl_mw_logging_download(board, 100, byref(download_handler))
    state.download_event.wait()

    # Clear memory and disconnect gracefully
    libmetawear.mbl_mw_logging_clear_entries(board)
    state.device.disconnect()
    print(f"{state.name} download complete and memory cleared.")

# --- 7. EXPORT TO CSV ---
for state in states:
    # Export quaternionss
    quat_file = f"{state.name}_quat.csv"
    with open(quat_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["epoch_ms", "q0_w", "q1_x", "q2_y", "q3_z"]) # OpenSim naming convention
        writer.writerows(state.quat_data)
    print(f"Saved {len(state.quat_data)} rows to {quat_file}")

    #Export Accelerometer data
    accel_file = f"{state.name}_accel.csv"
    with open(accel_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["epoch_ms", "acc_x", "acc_y", "acc_z"])
        writer.writerows(state.accel_data)
    print(f"Saved {len(state.accel_data)} rows to {accel_file}")


print("\nPipeline execution finished successfully.")
