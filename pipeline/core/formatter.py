"""OpenSense STO file generator.

Resamples and interpolates multi-sensor quaternion streams onto a uniform 100 Hz time grid,
normalizes quaternions to unit magnitude, and formats the output into strict OpenSim 4.4/4.5
compliant .sto files.
"""

from pathlib import Path
import numpy as np
import pandas as pd
from scipy.interpolate import interp1d


class OpenSenseFormatter:
    def __init__(self, data_rate_hz: float = 100.0, opensim_version: str = "4.4"):
        self.data_rate_hz = data_rate_hz
        self.dt = 1.0 / data_rate_hz
        self.opensim_version = opensim_version

    def interpolate_quaternion_stream(self, time_orig: np.ndarray, quats_orig: np.ndarray, target_time: np.ndarray) -> np.ndarray:
        """Interpolate quaternion components onto target uniform time grid and normalize."""
        # Ensure strictly ascending timestamps
        unique_mask = np.diff(time_orig, prepend=-np.inf) > 0
        t_clean = time_orig[unique_mask]
        q_clean = quats_orig[unique_mask]

        if len(t_clean) < 2:
            raise ValueError("Insufficient unique timestamp points for interpolation.")

        # Interpolate components linearly
        interpolator = interp1d(t_clean, q_clean, axis=0, kind='linear', fill_value='extrapolate')
        q_interp = interpolator(target_time)

        # Normalize quaternions to unit length
        norms = np.linalg.norm(q_interp, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        q_norm = q_interp / norms
        return q_norm

    def create_sto_file(self, synced_dfs: dict, output_file: Path) -> Path:
        """Merge all synchronized sensor dataframes into a uniform OpenSense .sto file."""
        if not synced_dfs:
            raise ValueError("No synchronized sensor dataframes provided.")

        sensor_names = list(synced_dfs.keys())
        print(f"\n=== Generating OpenSense .sto for {len(sensor_names)} Sensors ===")

        # Determine common valid time range
        min_start = 0.0
        max_end = min(df['time_s'].iloc[-1] for df in synced_dfs.values())

        if max_end <= min_start:
            raise ValueError(f"Invalid overlapping time duration across sensors (max_end={max_end:.2f}s).")

        num_frames = int(np.floor(max_end * self.data_rate_hz)) + 1
        target_time = np.linspace(0.0, (num_frames - 1) * self.dt, num_frames)

        print(f"  Target grid: 0.00s to {target_time[-1]:.2f}s ({num_frames} frames @ {self.data_rate_hz:.1f} Hz)")

        # Resample each sensor's quaternion stream
        sensor_strings = {}
        for name in sensor_names:
            df = synced_dfs[name]
            t_orig = df['time_s'].values
            q_cols = ['q0_w', 'q1_x', 'q2_y', 'q3_z']
            q_orig = df[q_cols].values

            q_resampled = self.interpolate_quaternion_stream(t_orig, q_orig, target_time)

            # Format as "w,x,y,z" strings
            formatted_quats = [
                f"{w:.7f},{x:.7f},{y:.7f},{z:.7f}"
                for w, x, y, z in q_resampled
            ]
            sensor_strings[name] = formatted_quats

        # Write OpenSense .sto file
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w') as f:
            f.write(f"DataRate={self.data_rate_hz:.6f}\n")
            f.write("DataType=Quaternion\n")
            f.write("version=3\n")
            f.write(f"OpenSimVersion={self.opensim_version}\n")
            f.write("endheader\n")

            # Column header
            header_cols = ["time"] + sensor_names
            f.write("\t".join(header_cols) + "\n")

            # Data rows
            for i, t in enumerate(target_time):
                row = [f"{t:.4f}"] + [sensor_strings[s][i] for s in sensor_names]
                f.write("\t".join(row) + "\n")

        print(f"  ✓ Successfully wrote {num_frames} frames to {output_file.name}")
        return output_file
