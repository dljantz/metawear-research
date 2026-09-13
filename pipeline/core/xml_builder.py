"""OpenSim Setup XML Generator.

Generates setup_imu_placer.xml and setup_imu_ik.xml for OpenSim/OpenSense GUI execution.
"""

from pathlib import Path


class XMLBuilder:
    def __init__(self, config: dict):
        self.config = config
        opensim_cfg = config.get("opensim", {})
        self.model_file = opensim_cfg.get("model_file", "Rajagopal_2015.osim")
        self.calibrated_model_file = opensim_cfg.get("calibrated_model_file", "Rajagopal_calibrated.osim")
        self.base_imu_label = opensim_cfg.get("base_imu_label", "pelvis_imu")
        self.base_heading_axis = opensim_cfg.get("base_heading_axis", "z")
        self.sensor_to_opensim_rotations = opensim_cfg.get("sensor_to_opensim_rotations", "-1.57079632679 0 0")

    def generate_imu_placer_xml(self, sto_filename: str, output_path: Path, available_sensors: list = None) -> Path:
        """Generate setup_imu_placer.xml for calibrating the model to the initial pose."""
        base_label = self.base_imu_label
        if available_sensors:
            if base_label not in available_sensors:
                # Fallback to torso_imu or pelvis_imu or first available sensor
                if "torso_imu" in available_sensors:
                    base_label = "torso_imu"
                elif "pelvis_imu" in available_sensors:
                    base_label = "pelvis_imu"
                else:
                    base_label = available_sensors[0]
                print(f"  Note: Base IMU '{self.base_imu_label}' not found in active sensors. Using '{base_label}' instead.")

        content = f"""<?xml version="1.0" encoding="UTF-8" ?>
<OpenSimDocument Version="40000">
    <IMUPlacer>
        <model_file>{self.model_file}</model_file>
        <orientation_file_name>{sto_filename}</orientation_file_name>
        <sensor_to_opensim_rotations>{self.sensor_to_opensim_rotations}</sensor_to_opensim_rotations>
        <base_imu_label>{base_label}</base_imu_label>
        <base_heading_axis>{self.base_heading_axis}</base_heading_axis>
        <output_model_file>{self.calibrated_model_file}</output_model_file>
    </IMUPlacer>
</OpenSimDocument>
"""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(content)
        print(f"  ✓ Generated IMU Placer XML -> {output_path.name}")
        return output_path

    def generate_imu_ik_xml(self, sto_filename: str, output_path: Path, max_time_s: float = 1000.0) -> Path:
        """Generate setup_imu_ik.xml for computing Inverse Kinematics joint angles."""
        content = f"""<?xml version="1.0" encoding="UTF-8" ?>
<OpenSimDocument Version="40000">
    <IMUInverseKinematicsTool>
        <model_file_name>{self.calibrated_model_file}</model_file_name>
        <orientations_file_name>{sto_filename}</orientations_file_name>
        <sensor_to_opensim_rotations>{self.sensor_to_opensim_rotations}</sensor_to_opensim_rotations>
        <time_range>0 {max_time_s:.2f}</time_range>
        <results_directory>IKResults</results_directory>
    </IMUInverseKinematicsTool>
</OpenSimDocument>
"""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(content)
        print(f"  ✓ Generated IMU IK XML -> {output_path.name}")
        return output_path
