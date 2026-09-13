# The purpose of this script is to perform a fun step in the
#	data pipeline -- run the 3D pythagorean theorem on each
#	raw acceleration data point to get a scalar magnitude.
#	Later this will be used to find the leading edge of 
#	each synchronization stomp.

import pandas as pd
import numpy as np
import glob
import sys

# Locate all accelerometer CSV files in the current directory
accel_files = glob.glob("*_accel.csv")

if not accel_files:
    print("No accelerometer CSV files found in the current directory.")
    sys.exit(1)

for file in accel_files:
    # Read the data into a Pandas DataFrame
    df = pd.read_csv(file)
    
    # Verify the expected columns exist before calculating
    required_columns = {'acc_x', 'acc_y', 'acc_z'}
    if not required_columns.issubset(df.columns):
        print(f"Skipping {file}: Missing requisite acceleration columns.")
        continue
        
    # Calculate the 3D vector magnitude via vectorized NumPy operations
    df['magnitude'] = np.sqrt(df['acc_x']**2 + df['acc_y']**2 + df['acc_z']**2)
    
    # Overwrite the original CSV file with the appended column
    df.to_csv(file, index=False)
    print(f"Successfully processed {file}: Appended 'magnitude' column.")

print("Magnitude extraction complete.")

