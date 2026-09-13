import pandas as pd
import numpy as np
import glob
import sys
import matplotlib.pyplot as plt
from scipy.signal import find_peaks

import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt


def find_leading_edges(file_path):
    df = pd.read_csv(file_path)

    if 'magnitude' not in df.columns:
        print(f"Error: 'magnitude' column missing in {file_path}.")
        return None

    # 1. Establish the resting baseline
    # Assuming the first 1 second (100 samples) is static standing
    resting_mean = df['magnitude'].iloc[0:100].mean()
    print("resting mean acceleration is " + str(resting_mean))
    resting_std = df['magnitude'].iloc[0:100].std()
    print("resting standard deviation for acceleration is " + str(resting_std))
    
    # 2. Identify the macro-peaks
    # We look for peaks highly elevated above the resting baseline (e.g., > 2.5g)
    # distance=1000 ensures we don't pick up two peaks from the same stomp (10 seconds apart)
    peak_threshold = resting_mean + (50 * resting_std) # found 50 standard deviations experimentally, it's just the threshold that seems to detect actual peaks without finding false ones
    peaks, _ = find_peaks(df['magnitude'], height=peak_threshold, distance=300) # here we are using the black box function find_peaks from scipy. first parameter is just the 1D array of data. height parameter is absolute minimum vertical value a peak must clear. A distance of 300, when recording at 100Hz, means the peaks must be 3s apart from each other.
    
    if len(peaks) < 2:
        print(f"Error: Could not locate two distinct stomps in {file_path}.")
        return None
        
    # Isolate the first (Start) and last (End) stomps
    start_peak_idx = peaks[0]
    end_peak_idx = peaks[-1]  # allows there to be multiple peaks, we'll just get the bookends
    
    # 3. The Reverse-Walk Algorithm
    # Define a strict threshold for when the signal has returned to baseline
    # could play with this if needed, seems fine for now
    baseline_threshold = resting_mean + (2 * resting_std)
    
    def reverse_walk(peak_idx):
        current_idx = peak_idx
        # Walk backward until the magnitude drops below the baseline threshold
        while current_idx > 0 and df.loc[current_idx, 'magnitude'] > baseline_threshold:
            current_idx -= 1
        return current_idx

    start_edge_idx = reverse_walk(start_peak_idx)
    end_edge_idx = reverse_walk(end_peak_idx)
    
    # Extract the precise epoch_ms for these edges
    t0_start = df.loc[start_edge_idx, 'epoch_ms']
    t1_end = df.loc[end_edge_idx, 'epoch_ms']
    
    # --- Visualization for Validation ---
    plt.figure(figsize=(12, 4))
    plt.plot(df['epoch_ms'], df['magnitude'], label='Vector Magnitude', color='lightgray')
    
    # Highlight the detected peaks and the calculated leading edges
    plt.scatter(df.loc[[start_peak_idx, end_peak_idx], 'epoch_ms'], 
                df.loc[[start_peak_idx, end_peak_idx], 'magnitude'], 
                color='red', label='Detected Peaks', zorder=5)
    plt.scatter([t0_start, t1_end], 
                df.loc[[start_edge_idx, end_edge_idx], 'magnitude'], 
                color='blue', label='Leading Edges (Sync Points)', zorder=5)
    
    plt.title(f"Sync Edge Detection: {file_path}")
    plt.xlabel("Epoch Time (ms)")
    plt.ylabel("Magnitude (g)")
    plt.legend()
    plt.tight_layout()
    plt.show()
    
    return {
        'file': file_path,
        'start_epoch': t0_start,
        'end_epoch': t1_end,
        'duration_ms': t1_end - t0_start
    }

# --- Execution ---
accel_files = glob.glob("*_accel.csv")
sync_data = {}

for file in accel_files:
    print(f"Analyzing {file}...")
    result = find_leading_edges(file)
    if result:
        sync_data[file] = result
        print(f"  Start Sync: {result['start_epoch']}")
        print(f"  End Sync:   {result['end_epoch']}")
        print(f"  Duration:   {result['duration_ms']} ms\n")

print("Edge detection complete.")


# --- Timestamp alignment in csv files ---
print("\nIf leading edge detection passed inspection, enter 'y' to proceed with timestamp alignment. This will modify the csv files so starting epochs all start at zero at the established synchronization point.")
response = input()

if response == 'y' or response == 'yes':
    # Truncate all data points in accel and quat files up to the sync event (leave the sync data point in)
    print("Heads up! If the following files do not appear in the same body segment order, this script will not work.")
    print(accel_files)
    quat_files = glob.glob("*_quat.csv")
    print(quat_files)
    print()

    for i in range(len(accel_files)):
        accel_file = accel_files[i]
        quat_file = quat_files[i]
        df = pd.read_csv(quat_file)
        
        starting_epoch = sync_data[accel_file]['start_epoch']
        ending_epoch = sync_data[accel_file]['end_epoch']
        # print(f"The starting timestamp for {quat_file[:-9]} is {starting_epoch}.")
        # print(f"Checkpoint Z: The ending timestamp for {quat_file[:-9]} is {ending_epoch}.")
        
        df = df[df['epoch_ms'] >= starting_epoch] # drop all the rows that precede first synchronizing event
        df = df[df['epoch_ms'] <= ending_epoch] # drop all rows that follow last synchronization event
        print("\nThe dataframe is now truncated so that data outside of bookending timestamps is eliminated:")
        print(df)
        print("Now adjusting all timestamps so the synchronization stomp is time zero.")
        df['epoch_ms'] = df['epoch_ms'] - starting_epoch # set sync event to new time zero

        adjusted_ending_epoch = ending_epoch - starting_epoch
        print(f"Adjusted ending epoch is {adjusted_ending_epoch}")
        print("New table:")
        print(df)
        
        print("Overwriting the old quat_file with truncated and zeroed version.")
        df.to_csv(quat_file, index=False)
        

