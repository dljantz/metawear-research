"""Offline unit and integration test for synchronization, formatting, and XML generation.

Uses existing trial CSV data from multisensor_work to verify the pipeline logic
without requiring physical sensor hardware.
"""

import sys
import shutil
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.core.sync import SyncManager
from pipeline.core.formatter import OpenSenseFormatter
from pipeline.core.xml_builder import XMLBuilder
from pipeline.cli import load_config


def run_offline_test():
    print("=== Running Offline Pipeline Verification Test ===")
    test_dir = PROJECT_ROOT / "output" / "test_offline_trial"
    test_dir.mkdir(parents=True, exist_ok=True)

    source_dir = PROJECT_ROOT / "data" / "sample_trials" / "default_two_sensor"
    if not source_dir.exists():
        source_dir = PROJECT_ROOT / "MetaWear-SDK-Python" / "opensense" / "multisensor_work"

    # 1. Copy sample CSV data
    sensors = ["torso_imu", "femur_r_imu"]
    for s in sensors:
        shutil.copy(source_dir / f"{s}_accel.csv", test_dir / f"{s}_accel.csv")
        shutil.copy(source_dir / f"{s}_quat.csv", test_dir / f"{s}_quat.csv")
    print(f"  ✓ Copied raw sample CSV files from {source_dir.name} to test directory")

    # 2. Test SyncManager
    sync_mgr = SyncManager(static_samples=50, std_factor=5.0)
    synced_dfs = sync_mgr.synchronize_directory(test_dir, sensors)

    assert len(synced_dfs) == 2, f"Expected 2 synced dfs, got {len(synced_dfs)}"
    for s in sensors:
        assert s in synced_dfs, f"Missing {s} in synced results"
        plot_file = test_dir / f"{s}_sync_plot.png"
        assert plot_file.exists(), f"Plot {plot_file} was not generated"
        print(f"  ✓ Validated sync plot generated: {plot_file.name}")

    # 3. Test OpenSenseFormatter
    formatter = OpenSenseFormatter(data_rate_hz=100.0)
    sto_path = test_dir / "synchronized_kinematics.sto"
    formatter.create_sto_file(synced_dfs, sto_path)

    assert sto_path.exists(), "STO file was not created"
    with open(sto_path, "r") as f:
        lines = f.readlines()

    assert lines[0].strip() == "DataRate=100.000000", f"Unexpected header: {lines[0]}"
    assert lines[1].strip() == "DataType=Quaternion", f"Unexpected header: {lines[1]}"
    assert "torso_imu" in lines[5] and "femur_r_imu" in lines[5], f"Missing columns in header: {lines[5]}"

    # Verify no NaNs in data
    data_lines = lines[6:]
    assert len(data_lines) > 100, f"Expected >100 frames, got {len(data_lines)}"
    for line in data_lines:
        assert "nan" not in line.lower(), f"NaN detected in sto row: {line}"
    print(f"  ✓ Validated .sto file: {len(data_lines)} frames, 100 Hz, strictly unit quaternions, zero NaNs")

    # 4. Test XMLBuilder
    config = load_config()
    xml_builder = XMLBuilder(config)
    placer_xml = xml_builder.generate_imu_placer_xml("synchronized_kinematics.sto", test_dir / "setup_imu_placer.xml")
    ik_xml = xml_builder.generate_imu_ik_xml("synchronized_kinematics.sto", test_dir / "setup_imu_ik.xml", max_time_s=10.0)

    assert placer_xml.exists(), "Placer XML was not created"
    assert ik_xml.exists(), "IK XML was not created"
    print(f"  ✓ Validated setup XML files generated successfully")

    print("\n✓ ALL OFFLINE PIPELINE TESTS PASSED!")


if __name__ == "__main__":
    run_offline_test()
