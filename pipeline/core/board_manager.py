"""Board manager module for MetaWear / MetaMotion MMS sensors.

Handles:
- Safe connection and disconnection with retry logic
- Sensor fusion configuration (IMUPlus or NDoF)
- Battery state monitoring and low-battery warnings
- Fault-tolerant sequential arming and offline flash logging
- Fault-tolerant sequential download and raw CSV export
- Board wipe and full reset
- LED blink diagnostics
"""

import sys
import time
import csv
from pathlib import Path
from threading import Event
from ctypes import cast, byref, POINTER, c_void_p

from mbientlab.metawear import MetaWear, libmetawear, parse_value
from mbientlab.metawear.cbindings import (
    SensorFusionMode,
    SensorFusionAccRange,
    SensorFusionGyroRange,
    SensorFusionData,
    FnVoid_VoidP_VoidP,
    FnVoid_VoidP_DataP,
    FnVoid_VoidP_UInt_UInt,
    FnVoid_VoidP_VoidP_VoidP_UInt,
    FnVoid_VoidP_UByte_Long_UByteP_UByte,
    LogDownloadHandler,
    LedPattern,
    LedPreset,
    LedColor,
    Const,
)


def get_fusion_mode(mode_str: str) -> int:
    """Map string mode to SensorFusionMode enum."""
    mode_str = mode_str.upper()
    if mode_str in ("IMUPLUS", "IMU_PLUS", "6AXIS", "6-AXIS"):
        return SensorFusionMode.IMU_PLUS
    elif mode_str in ("NDOF", "9AXIS", "9-AXIS"):
        return SensorFusionMode.NDOF
    elif mode_str == "COMPASS":
        return SensorFusionMode.COMPASS
    elif mode_str == "M4G":
        return SensorFusionMode.M4G
    return SensorFusionMode.IMU_PLUS


def get_acc_range(range_g: float) -> int:
    """Map numeric g-range to SensorFusionAccRange enum."""
    if range_g >= 16.0:
        return SensorFusionAccRange._16G
    elif range_g >= 8.0:
        return SensorFusionAccRange._8G
    elif range_g >= 4.0:
        return SensorFusionAccRange._4G
    return SensorFusionAccRange._2G


def get_gyro_range(dps: float) -> int:
    """Map numeric DPS to SensorFusionGyroRange enum."""
    if dps >= 2000.0:
        return SensorFusionGyroRange._2000DPS
    elif dps >= 1000.0:
        return SensorFusionGyroRange._1000DPS
    elif dps >= 500.0:
        return SensorFusionGyroRange._500DPS
    return SensorFusionGyroRange._250DPS


import re
import subprocess

def detect_active_hci_mac() -> str:
    """Detect the active/default Bluetooth controller's BD Address (MAC).

    Ensures Warble uses the correct Bluetooth adapter (e.g. hci1 / USB dongle)
    instead of defaulting to hci0, which triggers the FD_SETSIZE crash if hci0 is absent.
    """
    # 1. Try bluetoothctl list (preferred for default controller)
    try:
        out = subprocess.check_output(["bluetoothctl", "list"], text=True, stderr=subprocess.DEVNULL)
        lines = out.strip().splitlines()
        for line in lines:
            m = re.search(r"Controller\s+([0-9A-Fa-f:]{17})", line)
            if m:
                if "[default]" in line or len(lines) == 1:
                    return m.group(1).upper()
        if lines:
            m = re.search(r"Controller\s+([0-9A-Fa-f:]{17})", lines[0])
            if m:
                return m.group(1).upper()
    except Exception:
        pass

    # 2. Try hciconfig
    try:
        out = subprocess.check_output(["hciconfig"], text=True, stderr=subprocess.DEVNULL)
        m = re.search(r"BD Address:\s*([0-9A-Fa-f:]{17})", out)
        if m:
            return m.group(1).upper()
    except Exception:
        pass

    return None


class BoardManager:
    def __init__(self, config: dict):
        self.config = config
        self.sensors = [s for s in config.get("sensors", []) if s.get("enabled", True)]
        self.fusion_mode = get_fusion_mode(config.get("fusion_mode", "IMUPlus"))
        self.acc_range = get_acc_range(config.get("acc_range_g", 16.0))
        self.gyro_range = get_gyro_range(config.get("gyro_range_dps", 2000.0))
        self.hci_mac = config.get("hci_mac") or detect_active_hci_mac()
        if self.hci_mac:
            print(f"Active Bluetooth Adapter: {self.hci_mac}")

    def _connect_device(self, mac: str, max_retries: int = 3, timeout_s: float = 10.0) -> MetaWear:
        """Connect to a device with retry logic and robust error status handling."""
        last_err = None
        kwargs = {}
        if self.hci_mac:
            kwargs["hci_mac"] = self.hci_mac

        for attempt in range(1, max_retries + 1):
            try:
                device = MetaWear(mac, **kwargs)
                # Enforce BLE connection: prevent SDK from switching to USB mode when sensor is charging via laptop USB port
                type(device.usb).is_enumerated = property(lambda self: False)
                
                # Robust connect_async wrapper to handle integer status codes from MetaWear C++
                conn_evt = Event()
                conn_res = []

                def on_connect_completed(err):
                    conn_res.append(err)
                    conn_evt.set()

                device.connect_async(on_connect_completed)
                if not conn_evt.wait(timeout=timeout_s):
                    raise TimeoutError(f"Connection to {mac} timed out after {timeout_s}s")

                if conn_res and conn_res[0] is not None:
                    err = conn_res[0]
                    if isinstance(err, Exception):
                        raise err
                    elif err == 16:
                        raise TimeoutError("Device timed out during BLE handshake (MBL_MW_STATUS_ERROR_TIMEOUT)")
                    else:
                        raise RuntimeError(f"Device connection failed with status {err}")

                # Optimize BLE connection interval (7.5ms) for stable low-latency comms
                libmetawear.mbl_mw_settings_set_connection_parameters(device.board, 7.5, 7.5, 0, 6000)
                time.sleep(1.0)
                return device
            except Exception as e:
                last_err = e
                print(f"  [Attempt {attempt}/{max_retries}] Connecting to {mac} failed: {e}")
                time.sleep(1.5)
        raise RuntimeError(f"Could not connect to {mac} after {max_retries} attempts: {last_err}")

    def read_battery(self, board) -> dict:
        """Query board battery charge percentage and voltage."""
        batt_data = {"charge": None, "voltage": None}
        batt_evt = Event()

        def on_battery(ctx, p):
            val = parse_value(p)
            if hasattr(val, 'charge'):
                batt_data['charge'] = val.charge
                batt_data['voltage'] = val.voltage
            batt_evt.set()

        cb = FnVoid_VoidP_DataP(on_battery)
        signal = libmetawear.mbl_mw_settings_get_battery_state_data_signal(board)
        libmetawear.mbl_mw_datasignal_subscribe(signal, None, cb)
        libmetawear.mbl_mw_datasignal_read(signal)
        batt_evt.wait(timeout=2.0)
        libmetawear.mbl_mw_datasignal_unsubscribe(signal)
        return batt_data

    def test_connections(self, blink_seconds: float = 2.5):
        """Sequential connection & diagnostic test: checks battery, blinks LED."""
        print(f"\n=== Testing Connections for {len(self.sensors)} Enabled Sensors ===")
        results = {}

        pattern = LedPattern(repeat_count=Const.LED_REPEAT_INDEFINITELY)
        libmetawear.mbl_mw_led_load_preset_pattern(byref(pattern), LedPreset.BLINK)

        for sensor in self.sensors:
            name = sensor["name"]
            mac = sensor["mac"]
            segment = sensor.get("body_segment", "N/A")
            print(f"\nTesting {name} [{segment}] ({mac})...")
            try:
                dev = self._connect_device(mac, max_retries=2)
                info = dev.info
                batt = self.read_battery(dev.board)

                charge_str = f"{batt['charge']}%" if batt['charge'] is not None else "N/A"
                volt_str = f"{batt['voltage']}mV" if batt['voltage'] is not None else "N/A"
                print(f"  ✓ Connected! Firmware: {info.get('firmware')} | Battery: {charge_str} ({volt_str})")

                if batt['charge'] is not None and batt['charge'] < 20:
                    print(f"  ⚠️ WARNING: Low battery on {name} ({batt['charge']}%). Please charge before trial!")

                # Blink green
                libmetawear.mbl_mw_led_write_pattern(dev.board, byref(pattern), LedColor.GREEN)
                libmetawear.mbl_mw_led_play(dev.board)
                time.sleep(blink_seconds)
                libmetawear.mbl_mw_led_stop_and_clear(dev.board)
                time.sleep(0.3)

                dev.disconnect()
                time.sleep(0.8)
                results[name] = {
                    "status": "OK",
                    "battery": charge_str,
                    "voltage": volt_str
                }
                print(f"  ✓ {name} test passed.")
            except Exception as e:
                print(f"  ✗ {name} failed: {e}")
                results[name] = {"status": f"FAILED: {e}", "battery": "N/A", "voltage": "N/A"}

        print("\n=== Sensor Diagnostic Summary ===")
        print(f"{'Sensor Name':<16} {'Status':<12} {'Battery':<10} {'Voltage'}")
        print("-" * 52)
        for name, res in results.items():
            print(f"{name:<16} {res['status']:<12} {res['battery']:<10} {res['voltage']}")
        return results

    def wipe_all(self):
        """Sequential wipe and reset of all enabled sensors."""
        print(f"\n=== Wiping & Resetting {len(self.sensors)} Sensors ===")
        for sensor in self.sensors:
            name = sensor["name"]
            mac = sensor["mac"]
            print(f"\nWiping {name} ({mac})...")
            try:
                dev = self._connect_device(mac, max_retries=2)
                board = dev.board

                print("  Stopping active logging...")
                libmetawear.mbl_mw_logging_stop(board)
                time.sleep(0.3)

                print("  Flushing NAND flash...")
                libmetawear.mbl_mw_logging_flush_page(board)
                time.sleep(0.3)

                print("  Clearing log entries...")
                libmetawear.mbl_mw_logging_clear_entries(board)
                time.sleep(0.5)

                print("  Removing events & macros...")
                libmetawear.mbl_mw_event_remove_all(board)
                libmetawear.mbl_mw_macro_erase_all(board)
                time.sleep(0.3)

                print("  Tearing down board state...")
                libmetawear.mbl_mw_metawearboard_tear_down(board)
                time.sleep(0.3)

                print("  Sending debug reset...")
                libmetawear.mbl_mw_debug_reset_after_gc(board)
                try:
                    dev.disconnect()
                except Exception:
                    pass
                time.sleep(1.5)
                print(f"  ✓ {name} wiped and reset.")
            except Exception as e:
                print(f"  ✗ Failed to wipe {name}: {e}")

    def arm_sensors(self, allow_partial: bool = True) -> list:
        """Sequential arming: configure fusion and start logging on all sensors, then disconnect.

        If a sensor fails to respond (e.g. dead battery), it is skipped with a warning
        so the remaining healthy sensors can still record.
        """
        print(f"\n=== Arming {len(self.sensors)} Sensors Sequentially ===")
        print(f"  Mode: {self.config.get('fusion_mode', 'IMUPlus')}")
        print(f"  Acceleration Range: {self.config.get('acc_range_g', 16.0)}G")
        print(f"  Gyroscope Range: {self.config.get('gyro_range_dps', 2000.0)} DPS")

        armed_sensors = []
        failed_sensors = []
        callbacks = []

        for sensor in self.sensors:
            name = sensor["name"]
            mac = sensor["mac"]
            print(f"\nArming {name} ({mac})...")
            try:
                dev = self._connect_device(mac, max_retries=3)
                board = dev.board

                # Check battery before starting
                batt = self.read_battery(board)
                if batt['charge'] is not None:
                    print(f"  Battery: {batt['charge']}% ({batt['voltage']}mV)")
                    if batt['charge'] < 15:
                        print(f"  ⚠️ CRITICAL: {name} battery very low ({batt['charge']}%). May drop during trial!")

                # Clear stale entries
                libmetawear.mbl_mw_logging_stop(board)
                libmetawear.mbl_mw_logging_clear_entries(board)
                time.sleep(0.3)

                # Configure Sensor Fusion
                libmetawear.mbl_mw_sensor_fusion_set_mode(board, self.fusion_mode)
                libmetawear.mbl_mw_sensor_fusion_set_acc_range(board, self.acc_range)
                libmetawear.mbl_mw_sensor_fusion_set_gyro_range(board, self.gyro_range)
                libmetawear.mbl_mw_sensor_fusion_write_config(board)

                # Obtain data signals directly from fusion engine
                quat_signal = libmetawear.mbl_mw_sensor_fusion_get_data_signal(board, SensorFusionData.QUATERNION)
                acc_signal = libmetawear.mbl_mw_sensor_fusion_get_data_signal(board, SensorFusionData.CORRECTED_ACC)

                # Set up loggers
                setup_evt = Event()
                loggers = []

                def on_logger_created(ctx, ptr):
                    if ptr:
                        loggers.append(ptr)
                    setup_evt.set()

                logger_cb = FnVoid_VoidP_VoidP(on_logger_created)
                callbacks.append(logger_cb)

                setup_evt.clear()
                libmetawear.mbl_mw_datasignal_log(quat_signal, None, logger_cb)
                if not setup_evt.wait(timeout=5.0):
                    raise TimeoutError(f"Timeout creating quaternion logger for {name}")

                setup_evt.clear()
                libmetawear.mbl_mw_datasignal_log(acc_signal, None, logger_cb)
                if not setup_evt.wait(timeout=5.0):
                    raise TimeoutError(f"Timeout creating acceleration logger for {name}")

                # Start logging and sensor fusion
                libmetawear.mbl_mw_logging_start(board, 0)
                libmetawear.mbl_mw_sensor_fusion_enable_data(board, SensorFusionData.QUATERNION)
                libmetawear.mbl_mw_sensor_fusion_enable_data(board, SensorFusionData.CORRECTED_ACC)
                libmetawear.mbl_mw_sensor_fusion_start(board)

                # Disconnect to record untethered to flash
                dev.disconnect()
                time.sleep(0.8)

                armed_sensors.append(sensor)
                print(f"  ✓ {name} armed and logging.")
            except Exception as e:
                print(f"  ✗ FAILED TO ARM {name}: {e}")
                failed_sensors.append((name, str(e)))
                if not allow_partial:
                    raise e

        if failed_sensors:
            print(f"\n⚠️ WARNING: {len(failed_sensors)} sensor(s) failed to arm:")
            for s_name, err in failed_sensors:
                print(f"   - {s_name}: {err}")

        if not armed_sensors:
            raise RuntimeError("CRITICAL: No sensors were successfully armed. Aborting trial.")

        print(f"\n✓ Successfully armed {len(armed_sensors)} of {len(self.sensors)} sensors.")
        return armed_sensors

    def download_sensors(self, output_dir: Path, target_sensors: list = None) -> dict:
        """Sequential download: reconnect to each sensor, download logged data, save raw CSVs.

        Resilient: if a sensor dropped off mid-trial, it is marked as failed and skipped
        without crashing the download for the remaining healthy sensors.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        sensors_to_download = target_sensors if target_sensors is not None else self.sensors
        print(f"\n=== Downloading Data from {len(sensors_to_download)} Sensors Sequentially ===")
        downloaded_files = {}
        failed_downloads = []

        for sensor in sensors_to_download:
            name = sensor["name"]
            mac = sensor["mac"]
            print(f"\nConnecting to {name} ({mac}) for download...")

            quat_data = []
            accel_data = []
            callbacks = []

            try:
                dev = self._connect_device(mac, max_retries=3)
                board = dev.board

                # Stop fusion and logging
                libmetawear.mbl_mw_sensor_fusion_stop(board)
                libmetawear.mbl_mw_sensor_fusion_clear_enabled_mask(board)
                libmetawear.mbl_mw_logging_stop(board)

                # Flush NAND page buffer
                libmetawear.mbl_mw_logging_flush_page(board)
                time.sleep(0.8)

                # Query anonymous data signals on the reconnected board
                discovery_evt = Event()
                discovered = {}

                def signal_handler(ctx, b, signals, length):
                    discovered['length'] = length
                    discovered['signals'] = cast(signals, POINTER(c_void_p * length)) if signals else None
                    discovery_evt.set()

                sig_fn = FnVoid_VoidP_VoidP_VoidP_UInt(signal_handler)
                callbacks.append(sig_fn)

                libmetawear.mbl_mw_metawearboard_create_anonymous_datasignals(board, None, sig_fn)
                if not discovery_evt.wait(timeout=10.0):
                    raise TimeoutError(f"Timeout discovering anonymous signals on {name}")

                num_signals = discovered.get('length', 0)
                print(f"  Discovered {num_signals} active log streams on {name}")

                # Subscribe to data streams
                def data_handler(ctx, p):
                    val = parse_value(p)
                    epoch = p.contents.epoch
                    if hasattr(val, 'w'):
                        quat_data.append([epoch, val.w, val.x, val.y, val.z])
                    elif hasattr(val, 'x'):
                        accel_data.append([epoch, val.x, val.y, val.z])

                data_cb = FnVoid_VoidP_DataP(data_handler)
                callbacks.append(data_cb)

                if discovered.get('signals'):
                    for i in range(num_signals):
                        sig_ptr = discovered['signals'].contents[i]
                        libmetawear.mbl_mw_anonymous_datasignal_subscribe(sig_ptr, None, data_cb)

                # Download handler
                download_evt = Event()
                last_time = [time.time()]

                def progress_handler(ctx, entries_left, total_entries):
                    if total_entries > 0:
                        now = time.time()
                        if now - last_time[0] >= 3.0 or entries_left == 0:
                            pct = ((total_entries - entries_left) / total_entries) * 100
                            print(f"  [{name}] Download: {pct:.1f}% ({total_entries - entries_left}/{total_entries})")
                            last_time[0] = now
                    if entries_left == 0:
                        download_evt.set()

                prog_fn = FnVoid_VoidP_UInt_UInt(progress_handler)
                callbacks.append(prog_fn)

                download_handler = LogDownloadHandler(
                    context=None,
                    received_progress_update=prog_fn,
                    received_unknown_entry=cast(None, FnVoid_VoidP_UByte_Long_UByteP_UByte),
                    received_unhandled_entry=cast(None, FnVoid_VoidP_DataP)
                )

                print(f"  Downloading log entries from {name}...")
                libmetawear.mbl_mw_logging_download(board, 100, byref(download_handler))
                if not download_evt.wait(timeout=120.0):
                    print(f"  ⚠️ WARNING: Download timeout reached for {name}")

                # Clear flash entries after successful download
                libmetawear.mbl_mw_logging_clear_entries(board)
                time.sleep(0.5)

                dev.disconnect()
                time.sleep(0.8)

                # Save raw CSVs
                quat_file = output_dir / f"{name}_quat.csv"
                with open(quat_file, mode="w", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(["epoch_ms", "q0_w", "q1_x", "q2_y", "q3_z"])
                    writer.writerows(quat_data)
                print(f"  ✓ Saved {len(quat_data)} quat rows -> {quat_file.name}")

                accel_file = output_dir / f"{name}_accel.csv"
                with open(accel_file, mode="w", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(["epoch_ms", "acc_x", "acc_y", "acc_z"])
                    writer.writerows(accel_data)
                print(f"  ✓ Saved {len(accel_data)} accel rows -> {accel_file.name}")

                downloaded_files[name] = {
                    "quat_file": quat_file,
                    "accel_file": accel_file,
                    "num_quats": len(quat_data),
                    "num_accels": len(accel_data),
                }

            except Exception as e:
                print(f"  ✗ FAILED TO DOWNLOAD FROM {name}: {e}")
                failed_downloads.append((name, str(e)))

        if failed_downloads:
            print(f"\n⚠️ WARNING: {len(failed_downloads)} sensor(s) failed during download:")
            for s_name, err in failed_downloads:
                print(f"   - {s_name}: {err}")

        print(f"\n✓ Download complete: {len(downloaded_files)} of {len(sensors_to_download)} succeeded.")
        return downloaded_files
