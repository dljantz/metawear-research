import pandas as pd

# 1. Load the synchronized CSVs
torso_df = pd.read_csv('torso_imu_quat.csv')
femur_df = pd.read_csv('femur_r_imu_quat.csv')


# 2. Merge dataframes on the synchronized 'time' column
# Using a merge_asof ensures we can perform the merge without any data distortion via rounding
merged_df = pd.merge_asof(
    torso_df, 
    femur_df, 
    on='epoch_ms', 
    direction='nearest',
    tolerance=5,
    suffixes=('_torso', '_femur')
)


# 3. Format quaternion columns to comma-separated strings: "w,x,y,z"
# Adjust the column names (w, x, y, z) based on your actual CSV headers
merged_df['torso_imu'] = merged_df[['q0_w_torso', 'q1_x_torso', 'q2_y_torso', 'q3_z_torso']].astype(str).agg(','.join, axis=1)
merged_df['femur_r_imu'] = merged_df[['q0_w_femur', 'q1_x_femur', 'q2_y_femur', 'q3_z_femur']].astype(str).agg(','.join, axis=1)


# 4. Filter down to just the columns OpenSim needs
final_df = merged_df[['epoch_ms', 'torso_imu', 'femur_r_imu']]
final_df.rename(columns={'epoch_ms': 'time'}, inplace=True)  # this is needed to make OpenSim happy
final_df['time'] = final_df['time'] / 1000.0
print("here's your final dataframe:")
print(final_df)

# 5. Write the .sto file with the required OpenSim header
file_path = 'synchronized_kinematics.sto'
data_rate = 100.0

with open(file_path, 'w') as f:
    f.write(f"DataRate={data_rate}\n")
    f.write("DataType=Quaternion\n")
    f.write("version=3\n")
    f.write("OpenSimVersion=4.4\n")
    f.write("endheader\n")

# Append the dataframe using tab separators
final_df.to_csv(file_path, sep='\t', index=False, mode='a')

