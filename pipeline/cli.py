"""Unified CLI for the MetaMotion to OpenSim data pipeline.

Subcommands:
  test        - Test BLE connectivity and blink LEDs
  wipe        - Wipe flash memory and reset all sensors
  record      - Arm sensors, record trial, download, synchronize, and export OpenSim files
  process     - Synchronize and export OpenSim files from an existing raw CSV directory
  list        - Show configured sensors and statuses
"""

import os
import sys
import time
import json
import shutil
import argparse
from pathlib import Path
from datetime import datetime

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.core.sync import SyncManager
from pipeline.core.formatter import OpenSenseFormatter
from pipeline.core.xml_builder import XMLBuilder


def _get_board_manager_class():
    """Lazily load BoardManager so offline processing works without BLE dependencies."""
    try:
        from pipeline.core.board_manager import BoardManager
        return BoardManager
    except ImportError as e:
        raise RuntimeError(
            "Hardware BLE acquisition requires 'metawear' and Linux BLE drivers.\n"
            "To record or test sensors, use the lab's dedicated Linux acquisition hub.\n"
            f"Original import error: {e}"
        )


def load_config(config_path: Path = None) -> dict:
    if config_path is None:
        config_path = PROJECT_ROOT / "config" / "sensors_config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with open(config_path, "r") as f:
        return json.load(f)


def find_usb_drive() -> Path:
    """Attempt to detect a mounted USB drive in /media/$USER/ or /mnt/ (Linux) or removable drive letters (Windows)."""
    if os.name == "nt":
        try:
            import string
            from ctypes import windll
            candidates = []
            bitmask = windll.kernel32.GetLogicalDrives()
            for letter in string.ascii_uppercase:
                if bitmask & 1:
                    drive_path = f"{letter}:\\"
                    if windll.kernel32.GetDriveTypeW(drive_path) == 2:  # DRIVE_REMOVABLE
                        candidates.append(Path(drive_path))
                bitmask >>= 1
            return candidates[0] if candidates else None
        except Exception:
            return None

    user = os.environ.get("USER", "")
    candidates = []
    if user:
        media_dir = Path(f"/media/{user}")
        if media_dir.exists():
            for d in media_dir.iterdir():
                if d.is_dir():
                    candidates.append(d)
    mnt_dir = Path("/mnt")
    if mnt_dir.exists():
        for d in mnt_dir.iterdir():
            if d.is_dir() and d.name != "wsl":
                candidates.append(d)
    return candidates[0] if candidates else None


def cmd_list(args):
    config = load_config(args.config)
    sensors = config.get("sensors", [])
    print(f"\n=== Configured Sensors ({len(sensors)} total) ===")
    print(f"{'Status':<10} {'Sensor Name':<18} {'Segment':<15} {'MAC Address'}")
    print("-" * 65)
    for s in sensors:
        status = "ACTIVE" if s.get("enabled", True) else "DISABLED"
        print(f"{status:<10} {s['name']:<18} {s.get('body_segment', 'N/A'):<15} {s['mac']}")
    print(f"\nFusion Mode: {config.get('fusion_mode', 'IMUPlus')}")
    print(f"Sample Rate: {config.get('sample_rate_hz', 100.0)} Hz")


def cmd_ble_check(args):
    """Diagnose Bluetooth controllers, unblock rfkill, and power on all adapters."""
    print("\n=== Checking Bluetooth Hardware & Adapter Readiness ===")
    try:
        from pipeline.core.bluetooth_utils import ensure_bluetooth_ready, is_service_active, get_all_bluetooth_adapters
        service_status = "active" if is_service_active() else "inactive"
        print(f"  • Bluetooth Service (systemd): {service_status}")
        
        adapters = get_all_bluetooth_adapters(auto_power_on=True)
        if not adapters:
            print("  ✗ No Bluetooth adapters detected.")
            sys.exit(1)

        print(f"  • Detected Bluetooth Adapter(s): {len(adapters)}")
        for i, ad in enumerate(adapters, 1):
            powered_str = "YES (Powered & Ready, Blue LED ON)" if ad.get("powered") else "NO"
            default_tag = " [Default]" if ad.get("is_default") else ""
            print(f"    [{i}] MAC:     {ad['mac']}{default_tag}")
            print(f"        HCI:     {ad.get('hci', 'hci')}")
            print(f"        Name:    {ad.get('name', 'Unknown')}")
            print(f"        Powered: {powered_str}")

        if len(adapters) > 1:
            print(f"\n✓ Multiple adapters detected! Pipeline will run operations in parallel.")
        else:
            print(f"\n✓ Bluetooth adapter is powered ON and ready for MetaWear acquisitions.")
    except Exception as e:
        print(f"\n✗ Bluetooth Check Failed: {e}")
        sys.exit(1)


def cmd_test(args):
    try:
        config = load_config(args.config)
        BoardManager = _get_board_manager_class()
        bm = BoardManager(config)
        bm.test_connections(blink_seconds=args.blink_time)
    except Exception as e:
        if "Bluetooth" in type(e).__name__ or "Bluetooth" in str(e):
            print(f"\n[Bluetooth Error] {e}")
            sys.exit(1)
        raise


def cmd_wipe(args):
    try:
        config = load_config(args.config)
        BoardManager = _get_board_manager_class()
        bm = BoardManager(config)
        bm.wipe_all()
    except Exception as e:
        if "Bluetooth" in type(e).__name__ or "Bluetooth" in str(e):
            print(f"\n[Bluetooth Error] {e}")
            sys.exit(1)
        raise


def cmd_record(args):
    config = load_config(args.config)
    duration = args.duration or config.get("default_trial_duration_s", 30.0)

    # Setup output directory
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    trial_name = args.name or f"trial_{timestamp_str}"
    output_dir = Path(args.output_dir) if args.output_dir else PROJECT_ROOT / "output" / trial_name
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=======================================================")
    print(f"  STARTING TRIAL RECORDING: {trial_name}")
    print(f"  Duration: {duration:.1f} seconds")
    print(f"  Target Directory: {output_dir}")
    print(f"=======================================================")

    BoardManager = _get_board_manager_class()
    bm = BoardManager(config)
    sync_mgr = SyncManager()
    formatter = OpenSenseFormatter(data_rate_hz=config.get("sample_rate_hz", 100.0))
    xml_builder = XMLBuilder(config)

    # 1. Arm sensors sequentially (fault-tolerant)
    armed_sensors = bm.arm_sensors(allow_partial=True)

    # 2. Live trial execution
    print("\n" + "=" * 60)
    print("  >>> SENSORS ARMED & RECORDING TO ON-BOARD FLASH! <<<")
    print("  --> 1. Stand static for 1 second.")
    print("  --> 2. Perform your JUMP / LANDING sync gesture NOW!")
    print("  --> 3. Perform desired trial movements.")
    print("=" * 60 + "\n")

    start_time = time.time()
    while True:
        elapsed = time.time() - start_time
        remaining = duration - elapsed
        if remaining <= 0:
            break
        mins, secs = divmod(int(remaining), 60)
        sys.stdout.write(f"\r  Trial in progress... Remaining: {mins:02d}:{secs:02d}  ")
        sys.stdout.flush()
        time.sleep(1.0)

    print("\n\n*** TRIAL COMPLETE! Stand still for 1 second... ***")
    time.sleep(1.0)

    # 3. Halt logging immediately on all armed sensors to freeze flash memory
    bm.stop_sensors(target_sensors=armed_sensors)

    # 4. Download data (sequential by default with second-pass retry queue)
    downloaded_files = bm.download_sensors(output_dir, target_sensors=armed_sensors)
    if not downloaded_files:
        raise RuntimeError("No sensor data could be downloaded. Aborting downstream processing.")

    # 4. Synchronize streams via jump-landing leading edge
    successful_sensor_names = list(downloaded_files.keys())
    synced_dfs = sync_mgr.synchronize_directory(output_dir, successful_sensor_names)
    if not synced_dfs:
        raise RuntimeError("Synchronization failed for all sensors.")

    # 5. Format to OpenSense .sto
    sto_path = output_dir / "synchronized_kinematics.sto"
    formatter.create_sto_file(synced_dfs, sto_path)

    # 6. Generate OpenSim setup XML files
    xml_builder.generate_imu_placer_xml("synchronized_kinematics.sto", output_dir / "setup_imu_placer.xml", available_sensors=list(synced_dfs.keys()))
    max_duration = min(df['time_s'].iloc[-1] for df in synced_dfs.values())
    xml_builder.generate_imu_ik_xml("synchronized_kinematics.sto", output_dir / "setup_imu_ik.xml", max_time_s=max_duration)

    # 7. Optional USB copy
    usb_drive = find_usb_drive() if args.usb else None
    if usb_drive:
        usb_target = usb_drive / "OpenSim_Trials" / trial_name
        usb_target.mkdir(parents=True, exist_ok=True)
        print(f"\n=== Auto-Exporting to USB Drive: {usb_target} ===")
        for f in [sto_path, output_dir / "setup_imu_placer.xml", output_dir / "setup_imu_ik.xml"]:
            if f.exists():
                shutil.copy(f, usb_target / f.name)
        print(f"  ✓ Files successfully copied to USB flash drive!")

    print("\n" + "=" * 60)
    print("  ALL STEPS FINISHED SUCCESSFULLY!")
    print(f"  Results saved in: {output_dir}")
    print(f"  Sensors included: {', '.join(synced_dfs.keys())}")
    print("  Ready to open in OpenSim on Windows:")
    print("    1. synchronized_kinematics.sto")
    print("    2. setup_imu_placer.xml")
    print("    3. setup_imu_ik.xml")
    print("=" * 60 + "\n")


def cmd_process(args):
    """Reprocess existing raw CSVs in a directory without recollecting."""
    config = load_config(args.config)
    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")

    sync_mgr = SyncManager()
    formatter = OpenSenseFormatter(data_rate_hz=config.get("sample_rate_hz", 100.0))
    xml_builder = XMLBuilder(config)

    enabled_sensor_names = [s["name"] for s in config.get("sensors", []) if s.get("enabled", True)]
    available_sensors = [name for name in enabled_sensor_names if (input_dir / f"{name}_accel.csv").exists() and (input_dir / f"{name}_quat.csv").exists()]

    if not available_sensors:
        available_sensors = [f.name[:-10] for f in input_dir.glob("*_accel.csv") if (input_dir / f"{f.name[:-10]}_quat.csv").exists()]

    if not available_sensors:
        raise RuntimeError(f"No valid sensor CSV pairs found in {input_dir}")

    synced_dfs = sync_mgr.synchronize_directory(input_dir, available_sensors)
    if not synced_dfs:
        raise RuntimeError(f"No streams could be synchronized in {input_dir}")

    sto_path = input_dir / "synchronized_kinematics.sto"
    formatter.create_sto_file(synced_dfs, sto_path)

    xml_builder.generate_imu_placer_xml("synchronized_kinematics.sto", input_dir / "setup_imu_placer.xml", available_sensors=list(synced_dfs.keys()))
    max_duration = min(df['time_s'].iloc[-1] for df in synced_dfs.values())
    xml_builder.generate_imu_ik_xml("synchronized_kinematics.sto", input_dir / "setup_imu_ik.xml", max_time_s=max_duration)
    print(f"\n✓ Reprocessing complete for {input_dir}")


def main():
    parser = argparse.ArgumentParser(description="MetaMotion to OpenSim Data Acquisition & Processing CLI")
    parser.add_argument("--config", type=Path, default=None, help="Path to custom sensors_config.json")
    subparsers = parser.add_subparsers(dest="command", help="Pipeline subcommands")

    # ble-check
    subparsers.add_parser("ble-check", help="Check Bluetooth adapter, unblock rfkill, and power on antenna")

    # list
    subparsers.add_parser("list", help="List configured sensors and status")

    # test
    sub_test = subparsers.add_parser("test", help="Test BLE connectivity and quick-flash LEDs")
    sub_test.add_argument("--blink-time", type=float, default=0.5, help="Seconds to flash LED (default: 0.5s quick flash)")

    # wipe
    subparsers.add_parser("wipe", help="Wipe flash memory and reset all sensors")

    # record
    sub_rec = subparsers.add_parser("record", help="Record a trial (arm, log, download, sync, export)")
    sub_rec.add_argument("--duration", "-d", type=float, default=None, help="Trial duration in seconds")
    sub_rec.add_argument("--name", "-n", type=str, default=None, help="Custom trial name")
    sub_rec.add_argument("--output-dir", "-o", type=Path, default=None, help="Custom output directory")
    sub_rec.add_argument("--usb", action="store_true", help="Auto-copy export files to detected USB drive")

    # process
    sub_proc = subparsers.add_parser("process", help="Reprocess raw CSVs in an existing directory")
    sub_proc.add_argument("--input-dir", "-i", type=Path, required=True, help="Directory containing raw CSVs")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "ble-check":
        cmd_ble_check(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "test":
        cmd_test(args)
    elif args.command == "wipe":
        cmd_wipe(args)
    elif args.command == "record":
        cmd_record(args)
    elif args.command == "process":
        cmd_process(args)


if __name__ == "__main__":
    try:
        main()
        sys.stdout.flush()
        sys.stderr.flush()
        # Cleanly terminate immediately without triggering libwarble C++ static destructor segfaults during Python interpreter teardown
        os._exit(0)
    except KeyboardInterrupt:
        print("\n\n[Process Interrupted] Operation aborted by user. Exiting cleanly.")
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(130)
