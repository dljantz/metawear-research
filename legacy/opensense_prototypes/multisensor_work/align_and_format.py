import pandas as pd
import sys

# --- 1. CONFIGURATION ---
INPUT_FILES = {
    "torso_imu": "torso_imu_data.csv",
    "femur_r_imu": "femur_r_imu_data.csv"
}
OUTPUT_FILE = "opensense_aligned_orientations.sto"

dfs = {}
global_start = float('inf')

# --- 2. INGEST DATA & FIND GLOBAL START ---
for sensor_name, file_path in INPUT_FILES.items():
    try:
        df = pd.read_csv(file_path)
        dfs[sensor_name] = df
        
        # Find the absolute earliest timestamp across all files
        min_epoch = df['epoch_ms'].min()
        if min_epoch < global_start:
            global_start = min_epoch
            
    except FileNotFoundError:
        print(f"Error: Could not find {file_path}. Run the logging script first.")
        sys.exit(1)

processed_dfs = []

# --- 3. REFORMAT & NORMALIZE TIME ---
for sensor_name, df in dfs.items():
    # OpenSim expects quaternions formatted as "w,x,y,z"
    df[sensor_name] = df.apply(lambda row: f"{row['q0_w']},{row['q1_x']},{row['q2_y']},{row['q3_z']}", axis=1)
    
    # Normalize time to seconds from the global start, rounded to nearest 0.01s (100Hz)
    df['time'] = ((df['epoch_ms'] - global_start) / 1000.0).round(2)
    
    # Isolate the time and formatted quaternion columns
    df_clean = df[['time', sensor_name]].copy()
    
    # Drop duplicates if minor clock drift causes two epochs to round to the same hundredth
    df_clean = df_clean.drop_duplicates(subset=['time'])
    
    processed_dfs.append(df_clean)

# --- 4. MERGE & INTERPOLATE ---
merged_df = processed_dfs[0]
for i in range(1, len(processed_dfs)):
    merged_df = pd.merge(merged_df, processed_dfs[i], on='time', how='outer')

# Sort chronologically
merged_df = merged_df.sort_values('time')

# Forward and backward fill to bridge any tiny gaps caused by clock misalignment
merged_df.ffill(inplace=True) 
merged_df.bfill(inplace=True)

# --- 5. EXPORT TO OPENSENSE .STO ---
print(f"Writing aligned data to {OUTPUT_FILE}...")
with open(OUTPUT_FILE, "w") as f:
    # Strict OpenSense header requirements
    f.write("DataRate=100.000000\n") 
    f.write("DataType=Quaternion\n")
    f.write("version=3\n")
    f.write("OpenSimVersion=4.5\n")
    f.write("endheader\n")
    
    # Dynamic column headers
    sensor_names = list(INPUT_FILES.keys())
    f.write("time\t" + "\t".join(sensor_names) + "\n")
    
    # Data rows
    for index, row in merged_df.iterrows():
        f.write(f"{row['time']:.2f}")
        for name in sensor_names:
            f.write(f"\t{row[name]}")
        f.write("\n")

print("Alignment and formatting complete. Ready for OpenSim.")
