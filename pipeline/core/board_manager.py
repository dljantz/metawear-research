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
from threading import Event, Lock
from typing import Optional, Dict, List, Any
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


from pipeline.core.bluetooth_utils import ensure_bluetooth_ready, BluetoothAdapterError


class BoardManager:
    def __init__(self, config: dict):
        self.config = config
        self.sensors = [s for s in config.get("sensors", []) if s.get("enabled", True)]
        self.fusion_mode = get_fusion_mode(config.get("fusion_mode", "IMUPlus"))
        self.acc_range = get_acc_range(config.get("acc_range_g", 16.0))
        self.gyro_range = get_gyro_range(config.get("gyro_range_dps", 2000.0))
        self.parallel_downloads = bool(config.get("parallel_downloads", False))
        
        configured_hci = config.get("hci_mac")
        if configured_hci:
            if isinstance(configured_hci, (list, tuple)):
                self.hci_macs = [m.upper() for m in configured_hci]
            else:
                self.hci_macs = [configured_hci.upper()]
            self.hci_mac = self.hci_macs[0]
            print(f"Using configured Bluetooth Adapter(s): {', '.join(self.hci_macs)}")
        else:
            all_adapters = ensure_bluetooth_ready(auto_power_on=True, return_all=True)
            self.adapters = all_adapters
            self.hci_macs = [a["mac"] for a in all_adapters]
            self.hci_mac = self.hci_macs[0]
            if not self.parallel_downloads or len(self.hci_macs) == 1:
                ad = all_adapters[0]
                status_tag = "Powered & Ready, Blue LED ON" if ad.get("powered") else "Detected"
                tag = f"{ad.get('hci', 'hci')} | {ad.get('name', 'Bluetooth Adapter')}"
                print(f"Active Bluetooth Adapter: {self.hci_mac} ({tag}) [{status_tag}]")
                if len(self.hci_macs) > 1:
                    print(f"  (Sequential execution enabled: {len(self.hci_macs)} adapters detected, operating sequentially on primary {ad.get('hci', 'hci')})")
            else:
                print(f"✓ Detected {len(self.hci_macs)} Active Bluetooth Adapters for Parallel Operations:")
                for i, ad in enumerate(all_adapters, 1):
                    tag = f"{ad.get('hci', 'hci')} | {ad.get('name', 'Bluetooth Adapter')}"
                    print(f"   [{i}] {ad['mac']} ({tag})")

        # Persistent device cache keyed by (device_mac, hci_mac) to prevent C++ / ctypes GC corruption
        self.devices = {}
        self._callbacks = []
        self._print_lock = Lock()

    def _connect_device(self, mac: str, hci_mac: Optional[str] = None, max_retries: int = 3, timeout_s: float = 10.0) -> MetaWear:
        """Connect to a device with retry logic, instance caching, and robust error status handling."""
        target_hci = (hci_mac or self.hci_mac).upper()
        if not target_hci:
            raise BluetoothAdapterError(
                "Cannot connect: No active Bluetooth adapter is available.\n"
                "Please plug in your USB Bluetooth antenna."
            )
        last_err = None
        device_key = (mac.upper(), target_hci)

        # Reuse existing persistent device instance to avoid re-instantiation and GC corruption in libwarble
        device = self.devices.get(device_key)
        if device is None:
            device = MetaWear(mac, hci_mac=target_hci)
            # Enforce BLE connection: prevent SDK from switching to USB mode when sensor is charging via laptop USB port
            type(device.usb).is_enumerated = property(lambda self: False)
            self.devices[device_key] = device

        if device.is_connected:
            return device

        for attempt in range(1, max_retries + 1):
            try:
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

                # Optimize BLE connection interval: 7.5ms min, 15.0ms max, 0 latency, 4000ms supervision timeout
                # Satisfies Linux kernel BLE specs to prevent 'ignoring invalid connection parameters'
                libmetawear.mbl_mw_settings_set_connection_parameters(device.board, 7.5, 15.0, 0, 4000)
                time.sleep(0.5)
                return device
            except Exception as e:
                last_err = e
                print(f"  [Attempt {attempt}/{max_retries}] Connecting to {mac} failed: {e}")
                try:
                    device.disconnect()
                except Exception:
                    pass
                time.sleep(1.5)
        self.devices.pop(device_key, None)
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

    def test_connections(self, blink_seconds: float = 0.5):
        """Sequential connection & diagnostic test: checks battery, quick flash LED."""
        print(f"\n=== Testing Connections for {len(self.sensors)} Enabled Sensors ===")
        results = {}

        pattern = LedPattern(repeat_count=1)
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

                # Quick flash green LED
                libmetawear.mbl_mw_led_write_pattern(dev.board, byref(pattern), LedColor.GREEN)
                libmetawear.mbl_mw_led_play(dev.board)
                time.sleep(blink_seconds)
                libmetawear.mbl_mw_led_stop_and_clear(dev.board)
                time.sleep(0.2)

                dev.disconnect()
                time.sleep(0.5)
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

                print("  Clearing log entries (waiting 3.0s for SPI flash erase)...")
                libmetawear.mbl_mw_logging_clear_entries(board)
                time.sleep(3.0)

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

                # Proactively tear down any stale loggers/events/macros and wipe flash
                libmetawear.mbl_mw_logging_stop(board)
                libmetawear.mbl_mw_sensor_fusion_stop(board)
                time.sleep(0.2)
                libmetawear.mbl_mw_logging_flush_page(board)
                time.sleep(0.2)
                libmetawear.mbl_mw_logging_clear_entries(board)
                time.sleep(3.0)  # Physical erase of SPI NOR flash sectors requires ~2-3 seconds
                libmetawear.mbl_mw_event_remove_all(board)
                libmetawear.mbl_mw_macro_erase_all(board)
                libmetawear.mbl_mw_metawearboard_tear_down(board)
                time.sleep(0.5)

                # Configure Sensor Fusion
                libmetawear.mbl_mw_sensor_fusion_set_mode(board, self.fusion_mode)
                libmetawear.mbl_mw_sensor_fusion_set_acc_range(board, self.acc_range)
                libmetawear.mbl_mw_sensor_fusion_set_gyro_range(board, self.gyro_range)
                libmetawear.mbl_mw_sensor_fusion_write_config(board)
                time.sleep(0.5)

                # Obtain data signals directly from fusion engine
                quat_signal = libmetawear.mbl_mw_sensor_fusion_get_data_signal(board, SensorFusionData.QUATERNION)
                acc_signal = libmetawear.mbl_mw_sensor_fusion_get_data_signal(board, SensorFusionData.CORRECTED_ACC)

                # Set up loggers
                setup_evt = Event()
                loggers = []
                logger_err = [None]

                def on_logger_created(ctx, ptr):
                    if ptr:
                        loggers.append(ptr)
                    else:
                        logger_err[0] = "Board returned NULL logger pointer (logger slots full or dirty state)"
                    setup_evt.set()

                logger_cb = FnVoid_VoidP_VoidP(on_logger_created)
                self._callbacks.append(logger_cb)

                setup_evt.clear()
                libmetawear.mbl_mw_datasignal_log(quat_signal, None, logger_cb)
                if not setup_evt.wait(timeout=5.0) or len(loggers) < 1:
                    err_msg = logger_err[0] or f"Timeout creating quaternion logger for {name}"
                    raise TimeoutError(err_msg)

                setup_evt.clear()
                libmetawear.mbl_mw_datasignal_log(acc_signal, None, logger_cb)
                if not setup_evt.wait(timeout=5.0) or len(loggers) < 2:
                    err_msg = logger_err[0] or f"Timeout creating acceleration logger for {name}"
                    raise TimeoutError(err_msg)

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

    def stop_sensors(self, target_sensors: list = None) -> list:
        """Immediately halt logging and sensor fusion on all armed sensors.

        Freezes flash memory right after the trial countdown finishes, preventing
        sensors from continuing to record unwanted data during sequential/parallel downloads.
        """
        sensors_to_stop = target_sensors if target_sensors is not None else self.sensors
        print(f"\n=== Stopping Logging on {len(sensors_to_stop)} Sensors Immediately ===")

        stopped = []
        failed = []

        def _stop_single(sensor_info, assigned_hci):
            name = sensor_info["name"]
            mac = sensor_info["mac"]
            dev = None
            try:
                dev = self._connect_device(mac, hci_mac=assigned_hci, max_retries=2, timeout_s=8.0)
                board = dev.board
                libmetawear.mbl_mw_logging_stop(board)
                libmetawear.mbl_mw_sensor_fusion_stop(board)
                libmetawear.mbl_mw_logging_flush_page(board)
                time.sleep(0.2)
                dev.disconnect()
                time.sleep(0.4)
                with self._print_lock:
                    print(f"  ✓ {name} logging stopped.")
                return (name, True, None)
            except Exception as e:
                with self._print_lock:
                    print(f"  ⚠️ Warning: Could not stop {name} during sweep ({e}). Will stop during download.")
                if dev:
                    try:
                        dev.disconnect()
                    except Exception:
                        pass
                return (name, False, str(e))

        num_adapters = len(self.hci_macs)
        if self.parallel_downloads and num_adapters > 1:
            from concurrent.futures import ThreadPoolExecutor
            import queue

            sensor_q = queue.Queue()
            for s in sensors_to_stop:
                sensor_q.put(s)

            def stop_worker(adapter_mac):
                while True:
                    try:
                        sensor = sensor_q.get_nowait()
                    except queue.Empty:
                        break
                    s_name, ok, err = _stop_single(sensor, adapter_mac)
                    if ok:
                        stopped.append(s_name)
                    else:
                        failed.append((s_name, err))
                    sensor_q.task_done()

            with ThreadPoolExecutor(max_workers=num_adapters) as executor:
                futures = [executor.submit(stop_worker, mac) for mac in self.hci_macs]
                for f in futures:
                    f.result()
        else:
            for s in sensors_to_stop:
                s_name, ok, err = _stop_single(s, self.hci_mac)
                if ok:
                    stopped.append(s_name)
                else:
                    failed.append((s_name, err))

            # Quick sequential retry pass for any sensors that failed on the first sweep attempt
            if failed:
                retry_names = [f[0] for f in failed]
                failed = []
                for s in sensors_to_stop:
                    if s["name"] in retry_names:
                        time.sleep(0.5)
                        s_name, ok, err = _stop_single(s, self.hci_mac)
                        if ok:
                            stopped.append(s_name)
                        else:
                            failed.append((s_name, err))

        print(f"✓ Stop sweep complete: {len(stopped)} of {len(sensors_to_stop)} confirmed stopped.\n")
        return stopped

    def _download_single_sensor(self, sensor: dict, output_dir: Path, assigned_hci: str) -> dict:
        """Download logged data from a single sensor using a designated Bluetooth adapter."""
        name = sensor["name"]
        mac = sensor["mac"]
        with self._print_lock:
            print(f"\nConnecting to {name} ({mac}) for download...")

        quat_data = []
        accel_data = []
        dev = None

        try:
            dev = self._connect_device(mac, hci_mac=assigned_hci, max_retries=3)
            board = dev.board

            # 1. Query anonymous data signals on the reconnected board first
            discovery_evt = Event()
            discovered = {}

            def signal_handler(ctx, b, signals, length):
                discovered['length'] = length
                discovered['signals'] = cast(signals, POINTER(c_void_p * length)) if signals else None
                discovery_evt.set()

            sig_fn = FnVoid_VoidP_VoidP_VoidP_UInt(signal_handler)
            with self._print_lock:
                self._callbacks.append(sig_fn)

            libmetawear.mbl_mw_metawearboard_create_anonymous_datasignals(board, None, sig_fn)
            if not discovery_evt.wait(timeout=10.0):
                raise TimeoutError(f"Timeout discovering anonymous signals on {name}")

            num_signals = discovered.get('length', 0)
            with self._print_lock:
                print(f"  Discovered {num_signals} active log streams on {name}")

            # 2. Stop fusion and logging, flush page buffer (safe & idempotent)
            libmetawear.mbl_mw_logging_stop(board)
            libmetawear.mbl_mw_sensor_fusion_stop(board)
            libmetawear.mbl_mw_logging_flush_page(board)
            time.sleep(0.3)

            # Tracking variables for live feedback and stall detection
            download_evt = Event()
            last_print_time = [0.0]
            last_data_time = [time.time()]
            total_entries_count = [0]
            entries_left_count = [0]

            # 3. Subscribe to data streams
            def data_handler(ctx, p):
                last_data_time[0] = time.time()
                val = parse_value(p)
                epoch = p.contents.epoch
                if hasattr(val, 'w'):
                    quat_data.append([epoch, val.w, val.x, val.y, val.z])
                elif hasattr(val, 'x'):
                    accel_data.append([epoch, val.x, val.y, val.z])

                now = time.time()
                if total_entries_count[0] == 0 and now - last_print_time[0] >= 1.5:
                    with self._print_lock:
                        print(f"  [{name}] Receiving data stream... {len(quat_data)} quat, {len(accel_data)} accel samples")
                    last_print_time[0] = now

            data_cb = FnVoid_VoidP_DataP(data_handler)
            with self._print_lock:
                self._callbacks.append(data_cb)

            if discovered.get('signals'):
                for i in range(num_signals):
                    sig_ptr = discovered['signals'].contents[i]
                    libmetawear.mbl_mw_anonymous_datasignal_subscribe(sig_ptr, None, data_cb)

            # 4. Download handler with fine-grained progress updates
            def progress_handler(ctx, entries_left, total_entries):
                total_entries_count[0] = total_entries
                entries_left_count[0] = entries_left
                if total_entries == 0:
                    with self._print_lock:
                        print(f"  [{name}] Sensor reports 0 log entries to download.")
                    download_evt.set()
                    return
                downloaded = total_entries - entries_left
                pct = (downloaded / total_entries) * 100
                now = time.time()
                if now - last_print_time[0] >= 1.0 or entries_left == 0:
                    with self._print_lock:
                        print(f"  [{name}] Download: {pct:5.1f}% ({downloaded}/{total_entries} entries) | {len(quat_data)} quats, {len(accel_data)} accels")
                    last_print_time[0] = now
                if entries_left == 0:
                    download_evt.set()

            prog_fn = FnVoid_VoidP_UInt_UInt(progress_handler)
            with self._print_lock:
                self._callbacks.append(prog_fn)

            def unknown_entry_handler(ctx, id, epoch, data, length):
                pass
            unk_fn = FnVoid_VoidP_UByte_Long_UByteP_UByte(unknown_entry_handler)
            with self._print_lock:
                self._callbacks.append(unk_fn)

            download_handler = LogDownloadHandler(
                context=None,
                received_progress_update=prog_fn,
                received_unknown_entry=unk_fn,
                received_unhandled_entry=cast(None, FnVoid_VoidP_DataP)
            )

            with self._print_lock:
                print(f"  Downloading log entries from {name}...")
            # Request 100 notification updates for smooth, fine-grained progress reporting
            libmetawear.mbl_mw_logging_download(board, 100, byref(download_handler))

            download_start = time.time()
            stall_threshold_s = 20.0  # Stalled if no packets received for 20s
            start_timeout_s = 25.0    # Stalled if download never begins after 25s

            stalled = False
            stall_reason = ""
            while not download_evt.is_set():
                time_since_data = time.time() - last_data_time[0]
                elapsed = time.time() - download_start
                total_samples = len(quat_data) + len(accel_data)

                # Only declare stalled if incoming packet flow has completely frozen
                if total_samples > 0 and time_since_data > stall_threshold_s:
                    stall_reason = f"no new data packets received for {int(time_since_data)}s"
                    stalled = True
                    break

                if total_samples == 0 and elapsed > start_timeout_s:
                    stall_reason = f"no data packets received after {int(elapsed)}s"
                    stalled = True
                    break

                download_evt.wait(timeout=0.5)

            if stalled:
                with self._print_lock:
                    print(f"\n  ⚠️ [{name}] Download stalled: {stall_reason}.")
                    print(f"  [{name}] Discarding {len(quat_data)} quats / {len(accel_data)} accels to prevent data truncation.")
                    print(f"  [{name}] Sensor on-board flash is preserved for retry.")
                try:
                    libmetawear.mbl_mw_metawearboard_tear_down(board)
                except Exception:
                    pass
                try:
                    dev.disconnect()
                except Exception:
                    pass
                time.sleep(2.0)
                raise RuntimeError(
                    f"Download from {name} stalled after {len(quat_data)} quats ({stall_reason})"
                )

            # Reached here only if NOT stalled and cleanly completed 100%
            if total_entries_count[0] > 0:
                with self._print_lock:
                    print(f"  [{name}] Download: 100.0% ({total_entries_count[0]}/{total_entries_count[0]} entries) | {len(quat_data)} quats, {len(accel_data)} accels")

            # Clear flash entries and tear down board state only upon clean 100% completion
            libmetawear.mbl_mw_logging_clear_entries(board)
            time.sleep(3.0)  # Wait for physical SPI NOR flash sector erase
            libmetawear.mbl_mw_event_remove_all(board)
            libmetawear.mbl_mw_macro_erase_all(board)
            libmetawear.mbl_mw_metawearboard_tear_down(board)
            time.sleep(0.5)

            try:
                dev.disconnect()
            except Exception:
                pass
            time.sleep(1.0)

            # Save complete raw CSVs
            quat_file = output_dir / f"{name}_quat.csv"
            with open(quat_file, mode="w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["epoch_ms", "q0_w", "q1_x", "q2_y", "q3_z"])
                writer.writerows(quat_data)

            accel_file = output_dir / f"{name}_accel.csv"
            with open(accel_file, mode="w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["epoch_ms", "acc_x", "acc_y", "acc_z"])
                writer.writerows(accel_data)

            with self._print_lock:
                print(f"  ✓ Saved {len(quat_data)} quat rows -> {quat_file.name}")
                print(f"  ✓ Saved {len(accel_data)} accel rows -> {accel_file.name}")

            return {
                "sensor_name": name,
                "quat_file": quat_file,
                "accel_file": accel_file,
                "num_quats": len(quat_data),
                "num_accels": len(accel_data),
            }

        except (KeyboardInterrupt, BaseException) as exc:
            # Clean teardown on interrupt to prevent segfault in Warble C++ thread
            if dev is not None:
                try:
                    libmetawear.mbl_mw_logging_stop(dev.board)
                except Exception:
                    pass
                try:
                    dev.disconnect()
                except Exception:
                    pass
                time.sleep(0.5)
            raise exc

    def download_sensors(self, output_dir: Path, target_sensors: list = None) -> dict:
        """Download logged data from sensors with second-pass retry resilience.

        Runs sequentially by default to eliminate 2.4 GHz RF packet collisions and USB controller
        contention, or concurrently if 'parallel_downloads' is explicitly enabled in config.
        Any sensor that fails connection, discovery, or stalls is retried in a dedicated second pass.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        sensors_to_download = target_sensors if target_sensors is not None else self.sensors
        num_adapters = len(self.hci_macs)
        use_parallel = self.parallel_downloads and num_adapters > 1

        downloaded_files = {}
        failed_first_pass = []

        if use_parallel:
            print(f"\n=== Downloading Data from {len(sensors_to_download)} Sensors Concurrently ({num_adapters} Bluetooth Adapters) ===")
            from concurrent.futures import ThreadPoolExecutor
            import queue

            # Use a task queue so workers dynamically pull next sensor when ready
            sensor_q = queue.Queue()
            for s in sensors_to_download:
                sensor_q.put(s)

            def worker_loop(adapter_mac):
                while True:
                    try:
                        sensor = sensor_q.get_nowait()
                    except queue.Empty:
                        break
                    s_name = sensor["name"]
                    try:
                        res = self._download_single_sensor(sensor, output_dir, assigned_hci=adapter_mac)
                        downloaded_files[s_name] = res
                    except Exception as e:
                        with self._print_lock:
                            print(f"  ⚠️ [First Pass] Failed to download from {s_name}: {e}")
                            print(f"  [{s_name}] Sensor data preserved on flash. Added to Second-Pass Retry Queue.")
                        failed_first_pass.append(sensor)
                    finally:
                        sensor_q.task_done()

            with ThreadPoolExecutor(max_workers=num_adapters) as executor:
                futures = [executor.submit(worker_loop, mac) for mac in self.hci_macs]
                for f in futures:
                    f.result()
        else:
            print(f"\n=== Downloading Data from {len(sensors_to_download)} Sensors Sequentially ===")
            print(f"  Active Bluetooth Adapter: {self.hci_mac}")

            for sensor in sensors_to_download:
                name = sensor["name"]
                try:
                    res = self._download_single_sensor(sensor, output_dir, assigned_hci=self.hci_mac)
                    downloaded_files[name] = res
                except Exception as e:
                    print(f"  ⚠️ [First Pass] Failed to download from {name}: {e}")
                    print(f"  [{name}] Sensor data preserved on flash. Added to Second-Pass Retry Queue.")
                    failed_first_pass.append(sensor)

        # --- PASS 2: Dedicated Second-Pass Retry Queue ---
        if failed_first_pass:
            print("\n" + "=" * 60)
            print(f"  === SECOND-PASS RETRY QUEUE ({len(failed_first_pass)} Sensor(s) to Retry) ===")
            print("  Waiting 3.0s for Bluetooth adapter and radio channels to settle...")
            print("=" * 60)
            time.sleep(3.0)

            permanently_failed = []
            for sensor in failed_first_pass:
                name = sensor["name"]
                success = False
                max_retries = 2
                for attempt in range(1, max_retries + 1):
                    print(f"\n[Second-Pass Retry {attempt}/{max_retries}] Retrying download for {name} ({sensor['mac']})...")
                    try:
                        res = self._download_single_sensor(sensor, output_dir, assigned_hci=self.hci_mac)
                        downloaded_files[name] = res
                        success = True
                        print(f"  ✓ [Second-Pass Success] {name} successfully downloaded on retry attempt {attempt}!")
                        break
                    except Exception as e:
                        print(f"  ✗ [Second-Pass Attempt {attempt}/{max_retries}] Retry failed for {name}: {e}")
                        time.sleep(2.0)

                if not success:
                    permanently_failed.append(name)

            if permanently_failed:
                print(f"\n⚠️ WARNING: {len(permanently_failed)} sensor(s) permanently failed download after retries:")
                for s_name in permanently_failed:
                    print(f"   - {s_name}")
            else:
                print(f"\n✓ Second-Pass Retry Queue recovered all previously failed sensors!")

        print(f"\n✓ Download complete: {len(downloaded_files)} of {len(sensors_to_download)} succeeded.")
        return downloaded_files
