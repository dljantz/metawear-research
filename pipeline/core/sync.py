"""Synchronization module for MetaMotion IMU data.

Detects jump-landing impact shocks across multiple sensors, extracts leading edges,
and aligns quaternion time streams to a unified t=0 synchronization event.
"""

from pathlib import Path
import numpy as np
import pandas as pd
from scipy.signal import find_peaks
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend to save PNGs without GUI interruption
import matplotlib.pyplot as plt


class SyncManager:
    def __init__(self, static_samples: int = 100, min_peak_distance: int = 200, std_factor: float = 8.0):
        self.static_samples = static_samples
        self.min_peak_distance = min_peak_distance
        self.std_factor = std_factor

    def compute_magnitude(self, accel_df: pd.DataFrame) -> pd.Series:
        """Calculate 3D Euclidean magnitude from raw acceleration axes."""
        for col in ('acc_x', 'acc_y', 'acc_z'):
            if col not in accel_df.columns:
                raise ValueError(f"Missing required acceleration column '{col}'")
        return np.sqrt(accel_df['acc_x']**2 + accel_df['acc_y']**2 + accel_df['acc_z']**2)

    def detect_sync_edges(self, accel_df: pd.DataFrame, sensor_name: str, plot_path: Path = None) -> dict:
        """Identify start and optional end sync leading edges using reverse-walk."""
        df = accel_df.copy()
        if 'magnitude' not in df.columns:
            df['magnitude'] = self.compute_magnitude(df)

        if len(df) <= self.static_samples:
            raise ValueError(f"Insufficient samples ({len(df)}) in {sensor_name} to establish baseline.")

        # 1. Establish resting baseline (assumed static standing during initial samples)
        resting_mean = df['magnitude'].iloc[0:self.static_samples].mean()
        resting_std = df['magnitude'].iloc[0:self.static_samples].std()
        if resting_std < 1e-4:
            resting_std = 0.05  # Prevent division by zero or overly tight threshold

        # 2. Identify macro peaks (jump landing / impact stomp)
        # Using adaptive threshold: resting_mean + (std_factor * std), with absolute minimum of 2.5g
        peak_threshold = max(resting_mean + (self.std_factor * resting_std), 2.5)
        peaks, _ = find_peaks(df['magnitude'], height=peak_threshold, distance=self.min_peak_distance)

        if len(peaks) == 0:
            # Fallback with relaxed threshold if jump was gentle
            fallback_threshold = max(resting_mean + 3.0 * resting_std, 1.8)
            peaks, _ = find_peaks(df['magnitude'], height=fallback_threshold, distance=self.min_peak_distance)
            if len(peaks) == 0:
                print(f"  WARNING: [{sensor_name}] No sync peaks found. Defaulting t0 to earliest timestamp.")
                return {
                    'start_epoch': df['epoch_ms'].iloc[0],
                    'end_epoch': df['epoch_ms'].iloc[-1],
                    'duration_ms': df['epoch_ms'].iloc[-1] - df['epoch_ms'].iloc[0],
                    'peaks': []
                }

        start_peak_idx = peaks[0]
        end_peak_idx = peaks[-1] if len(peaks) > 1 else None

        # 3. Reverse-walk to leading edge (threshold = mean + 2 * std)
        baseline_thresh = resting_mean + (2.0 * resting_std)

        def reverse_walk(peak_idx):
            cur = peak_idx
            while cur > 0 and df.loc[cur, 'magnitude'] > baseline_thresh:
                cur -= 1
            return cur

        start_edge_idx = reverse_walk(start_peak_idx)
        t0_start = df.loc[start_edge_idx, 'epoch_ms']

        if end_peak_idx is not None and end_peak_idx != start_peak_idx:
            end_edge_idx = reverse_walk(end_peak_idx)
            t1_end = df.loc[end_edge_idx, 'epoch_ms']
        else:
            end_edge_idx = len(df) - 1
            t1_end = df.loc[end_edge_idx, 'epoch_ms']

        # 4. Generate validation plot
        if plot_path:
            plt.figure(figsize=(10, 4))
            plt.plot(df['epoch_ms'], df['magnitude'], label='Vector Magnitude', color='lightgray', linewidth=1)
            plt.axhline(resting_mean, color='green', linestyle='--', alpha=0.6, label='Resting Baseline')
            plt.axhline(peak_threshold, color='orange', linestyle=':', alpha=0.6, label='Peak Threshold')

            plot_peaks = [start_peak_idx] if end_peak_idx is None else [start_peak_idx, end_peak_idx]
            plt.scatter(df.loc[plot_peaks, 'epoch_ms'], df.loc[plot_peaks, 'magnitude'],
                        color='red', s=40, label='Detected Peak', zorder=5)

            plot_edges = [start_edge_idx] if end_peak_idx is None else [start_edge_idx, end_edge_idx]
            plt.scatter(df.loc[plot_edges, 'epoch_ms'], df.loc[plot_edges, 'magnitude'],
                        color='blue', s=40, label='Leading Edge (Sync)', zorder=6)

            plt.title(f"Sync Edge Detection: {sensor_name}")
            plt.xlabel("Epoch Time (ms)")
            plt.ylabel("Magnitude (g)")
            plt.legend(loc='upper right', fontsize=8)
            plt.tight_layout()
            plt.savefig(plot_path, dpi=120)
            plt.close()

        return {
            'start_epoch': t0_start,
            'end_epoch': t1_end,
            'duration_ms': t1_end - t0_start,
            'peaks': peaks.tolist()
        }

    def synchronize_directory(self, data_dir: Path, sensor_names: list, use_bookend: bool = False) -> dict:
        """Synchronize all sensors in a directory using pair-matched CSVs."""
        print(f"\n=== Synchronizing {len(sensor_names)} Sensor Streams in {data_dir.name} ===")
        synced_quats = {}
        sync_info = {}

        # First pass: detect leading edges for each sensor
        for name in sensor_names:
            accel_file = data_dir / f"{name}_accel.csv"
            quat_file = data_dir / f"{name}_quat.csv"

            if not accel_file.exists() or not quat_file.exists():
                print(f"  WARNING: Missing CSV pair for {name} ({accel_file.name} / {quat_file.name}). Skipping.")
                continue

            accel_df = pd.read_csv(accel_file)
            quat_df = pd.read_csv(quat_file)

            plot_path = data_dir / f"{name}_sync_plot.png"
            edge_res = self.detect_sync_edges(accel_df, name, plot_path)
            sync_info[name] = edge_res

            t0 = edge_res['start_epoch']
            t1 = edge_res['end_epoch']

            print(f"  [{name}] Sync t0: {t0} ms (plot -> {plot_path.name})")

            # Truncate and shift quaternion data so t0 = 0.0s
            df_sync = quat_df[quat_df['epoch_ms'] >= t0].copy()
            if use_bookend and edge_res.get('duration_ms', 0) > 3000:
                df_sync = df_sync[df_sync['epoch_ms'] <= t1]

            # Convert to seconds relative to sync event
            df_sync['time_s'] = (df_sync['epoch_ms'] - t0) / 1000.0

            # Save synchronized intermediate quat CSV
            sync_quat_file = data_dir / f"{name}_quat_synced.csv"
            df_sync.to_csv(sync_quat_file, index=False)

            synced_quats[name] = df_sync

        return synced_quats
