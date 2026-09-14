# Android MetaMotion Data Acquisition & OpenSense Pipeline Specification

> **Bridge Context for Antigravity Agents**:
> - **Originating Project**: `metawear-research` (`/home/david-jantz/metawear-research`)
> - **Source Conversation ID**: `6d9cb595-a44a-42ad-98be-44f990f9195e`
> - **Target Application**: Native Android app (Kotlin + Jetpack Compose) for field IMU data collection and automated OpenSim/OpenSense pre-processing.

---

## 1. Executive Summary & Objective

This specification provides the technical blueprint for developing a bespoke Android application that replaces the Linux command-line data acquisition hub. 

The Android app will allow researchers to:
1. Carry a bag of MbientLab MetaMotion (MMS) sensors into any field environment with an Android phone.
2. Select desired sensor channels and recording duration through a clean mobile UI.
3. Sequentially connect, check battery levels, configure 6-axis on-board sensor fusion, and arm the sensors for untethered on-board flash logging.
4. Execute a synchronized trial initiated by an impact stomp/jump landing.
5. Sequentially download flash log data back to the smartphone over Bluetooth Low Energy (BLE).
6. Execute the pre-OpenSense mathematical pipeline directly on-device in native Kotlin (temporal alignment, impact shock leading-edge detection, 100 Hz quaternion linear interpolation, unit normalization).
7. Export the resulting OpenSense `.sto` kinematics file, OpenSim setup XML files, and raw CSVs to Google Drive, OneDrive, or local storage using the Android system Sharesheet.

---

## 2. Hardware Layout & Sensor Configuration

Sensors are mapped to anatomical segments as configured in the laboratory environment:

| Sensor Name | Anatomical Segment | Bluetooth MAC Address | Default State | Role in OpenSense Model |
| :--- | :--- | :--- | :--- | :--- |
| `torso_imu` | Torso | `EE:B2:BC:10:22:7D` | **Enabled** | Upper body orientation |
| `pelvis_imu` | Pelvis | `E5:50:E6:BC:8A:B1` | **Enabled** | **Base IMU** (Heading reference) |
| `femur_r_imu` | Right Thigh | `C2:DC:F0:FE:91:0E` | **Enabled** | Right hip kinematics |
| `femur_l_imu` | Left Thigh | `D4:90:44:9B:41:E0` | **Enabled** | Left hip kinematics |
| `tibia_r_imu` | Right Shank | `EC:A6:60:F5:9D:23` | **Enabled** | Right knee kinematics |
| `tibia_l_imu` | Left Shank | `EF:06:D4:57:BC:22` | **Enabled** | Left knee kinematics |
| `head_imu` | Head | `CC:DD:59:8A:67:7A` | **Enabled** | Head tracking |

### Hardware Acquisition Parameters:
- **Sensor Fusion Mode**: `IMUPlus` (6-axis on-board sensor fusion: Accelerometer + Gyroscope; immune to indoor magnetic field distortions).
- **Sampling Frequency**: `100.0 Hz`.
- **Accelerometer Range**: `±16.0 g`.
- **Gyroscope Range**: `±2000.0 DPS`.
- **BLE Connection Interval**: `7.5 ms` minimum / `7.5 ms` maximum (`0` latency, `6000 ms` supervision timeout) for rapid throughput and handshake stability.

---

## 3. End-to-End Acquisition & Trial Lifecycle

```
    ┌─────────────────────────┐
    │ 1. Diagnostic / Battery │  Sequential connect -> Read Battery (%) & Voltage -> Blink LED
    └────────────┬────────────┘
                 ▼
    ┌─────────────────────────┐
    │   2. Sequential Arming  │  Connect -> Stop old log -> Write Fusion cfg -> Create Loggers
    └────────────┬────────────┘  Start Logging -> Start Fusion -> Disconnect (Untethered)
                 ▼
    ┌─────────────────────────┐
    │    3. Movement Trial    │  1s Static -> Sync Stomp/Jump -> Movement (T sec) -> 1s Static
    └────────────┬────────────┘
                 ▼
    ┌─────────────────────────┐
    │ 4. Sequential Download  │  Reconnect -> Stop Fusion & Log -> FLUSH NAND FLASH PAGE
    └────────────┬────────────┘  Discover Anonymous Signals -> Stream Log Data -> Clear Flash -> Disconnect
                 ▼
    ┌─────────────────────────┐
    │ 5. On-Device Processing │  Detect Sync t0 -> 100 Hz Interpolate -> Normalize Quaternions
    └────────────┬────────────┘  Generate .sto -> Generate setup_imu_placer.xml & setup_imu_ik.xml
                 ▼
    ┌─────────────────────────┐
    │   6. Cloud Export/Share │  Launch Android Sharesheet -> Save to Google Drive / OneDrive
    └─────────────────────────┘
```

### Detailed BLE State Machine per Sensor

#### Phase A: Arming (Pre-Trial)
For each active sensor in sequence:
1. Connect via BLE using MAC address (`BluetoothDevice.connectGatt`).
2. Read battery status (`mbl_mw_settings_get_battery_state_data_signal`). If `< 20%`, warn researcher.
3. Stop any existing logging and clear stale flash entries (`mbl_mw_logging_stop`, `mbl_mw_logging_clear_entries`).
4. Configure sensor fusion:
   - Mode: `IMU_PLUS`
   - Accel range: `16G`
   - Gyro range: `2000DPS`
5. Create flash loggers for two data signals:
   - `SensorFusionData.QUATERNION`
   - `SensorFusionData.CORRECTED_ACC`
6. Start flash logging (`mbl_mw_logging_start`).
7. Enable fusion data channels and start fusion engine (`mbl_mw_sensor_fusion_start`).
8. Disconnect BLE link so sensor records untethered to onboard flash.

#### Phase B: Motion Execution
1. App provides audio/visual countdown.
2. Participant stands static for 1.0 second.
3. Participant performs synchronization impact stomp or jump-landing.
4. Participant performs the trial activities for user-specified duration $T$ (e.g., 30s to 120s).
5. Participant stands still for 1.0 second upon completion tone.

#### Phase C: Download & Teardown
For each armed sensor in sequence:
1. Reconnect via BLE.
2. Stop sensor fusion (`mbl_mw_sensor_fusion_stop`) and clear enabled masks.
3. Stop flash logging (`mbl_mw_logging_stop`).
4. **CRITICAL FIRMWARE STEP**: Flush NAND flash page buffer (`mbl_mw_logging_flush_page`). *If this step is skipped, the final page of sensor data (~1–2 seconds) remains buffered in RAM and is truncated from download!*
5. Query anonymous data signals on the reconnected board (`mbl_mw_metawearboard_create_anonymous_datasignals`).
6. Map discovered anonymous signals to Quaternion and Corrected Acceleration subscribers.
7. Initiate download (`mbl_mw_logging_download`) with progress callback updating the UI (0% to 100%).
8. Clear flash entries (`mbl_mw_logging_clear_entries`).
9. Disconnect BLE link.
10. Write raw CSV files:
    - `<sensor>_quat.csv` with header: `epoch_ms,q0_w,q1_x,q2_y,q3_z`
    - `<sensor>_accel.csv` with header: `epoch_ms,acc_x,acc_y,acc_z`

---

## 4. Native Pre-OpenSense Mathematical Engine

All pre-processing is executed natively in Kotlin without relying on Python or heavy C-libraries.

### Algorithm 1: Euclidean 3D Acceleration Magnitude
For each acceleration sample $(a_x, a_y, a_z)$:
$$M(t) = \sqrt{a_x^2 + a_y^2 + a_z^2}$$

### Algorithm 2: Resting Baseline Estimation
Using the first $N = 100$ static samples (representing the 1-second static pause):
$$\mu = \frac{1}{N} \sum_{i=1}^{N} M_i$$
$$\sigma = \sqrt{\frac{1}{N-1} \sum_{i=1}^{N} (M_i - \mu)^2}$$
*Guardrail*: If $\sigma < 0.05\text{ g}$, clamp $\sigma = 0.05\text{ g}$ to avoid overly sensitive thresholds.

### Algorithm 3: Synchronization Peak & Leading Edge Detection
1. **Primary Peak Detection**:
   $$\text{Threshold}_{\text{peak}} = \max(\mu + 8.0 \cdot \sigma, 2.5\text{ g})$$
   Search for local maxima in $M(t)$ with height $\ge \text{Threshold}_{\text{peak}}$ and minimum sample separation of 200 samples (2.0 seconds at 100 Hz).
2. **Fallback Peak Detection** (if no peaks detected):
   $$\text{Threshold}_{\text{fallback}} = \max(\mu + 3.0 \cdot \sigma, 1.8\text{ g})$$
3. **Leading Edge Reverse-Walk**:
   Let the index of the first sync peak be $i_{\text{peak}}$.
   $$\text{Threshold}_{\text{baseline}} = \mu + 2.0 \cdot \sigma$$
   Walk backward:
   $$\text{while } i > 0 \text{ and } M_i > \text{Threshold}_{\text{baseline}}: i \leftarrow i - 1$$
   The leading edge timestamp $t_0 = \text{epoch\_ms}[i]$ defines the exact moment of foot impact.
4. **Temporal Alignment**:
   For all quaternion rows:
   $$t_{\text{rel}} = \frac{\text{epoch\_ms} - t_0}{1000.0} \quad (\text{seconds})$$
   Discard any rows where $t_{\text{rel}} < 0.0\text{ s}$.

### Algorithm 4: 100 Hz Linear Interpolation & Normalization
1. Determine common valid trial duration:
   $$T_{\max} = \min_{s \in \text{Sensors}} \left( t_{\text{rel, last}}^{(s)} \right)$$
2. Construct uniform time grid:
   $$N_{\text{frames}} = \lfloor T_{\max} \cdot 100.0 \rfloor + 1$$
   $$t_k = k \cdot 0.010 \quad \text{for } k = 0, 1, \dots, N_{\text{frames}} - 1$$
3. For each sensor, perform 1D linear interpolation on each quaternion component $(q_0, q_1, q_2, q_3)$ at each target point $t_k$:
   $$q(t_k) = q_a + (q_b - q_a) \frac{t_k - t_a}{t_b - t_a}$$
4. **Unit Quaternion Normalization**:
   $$q_{\text{norm}}(t_k) = \frac{q(t_k)}{\|q(t_k)\|} = \frac{(w, x, y, z)}{\sqrt{w^2 + x^2 + y^2 + z^2}}$$
   *(Zero values default to $w=1.0, x=0, y=0, z=0$)*.

---

## 5. Output File Formats

### 1. OpenSense Kinematics File (`synchronized_kinematics.sto`)
Strictly compliant with OpenSim 4.4 / 4.5:
```text
DataRate=100.000000
DataType=Quaternion
version=3
OpenSimVersion=4.4
endheader
time	torso_imu	pelvis_imu	femur_r_imu	femur_l_imu	tibia_r_imu	tibia_l_imu	head_imu
0.0000	0.9854120,0.0124010,0.1695430,-0.0084320	...
0.0100	0.9854100,0.0124150,0.1695500,-0.0084300	...
```
- Line endings: `\n`.
- Column delimiter: Tab (`\t`).
- Header: exactly 6 lines (ending with `endheader\n`).
- Quaternions: formatted as comma-separated string `w,x,y,z` to 7 decimal places.

### 2. Model Calibration Tool XML (`setup_imu_placer.xml`)
```xml
<?xml version="1.0" encoding="UTF-8" ?>
<OpenSimDocument Version="40000">
    <IMUPlacer>
        <model_file>Rajagopal_2015.osim</model_file>
        <orientation_file_name>synchronized_kinematics.sto</orientation_file_name>
        <sensor_to_opensim_rotations>-1.57079632679 0 0</sensor_to_opensim_rotations>
        <base_imu_label>pelvis_imu</base_imu_label>
        <base_heading_axis>z</base_heading_axis>
        <output_model_file>Rajagopal_calibrated.osim</output_model_file>
    </IMUPlacer>
</OpenSimDocument>
```
*Note*: If `pelvis_imu` was disabled for a trial, the generator automatically falls back to `torso_imu` or the first available sensor.

### 3. Inverse Kinematics Tool XML (`setup_imu_ik.xml`)
```xml
<?xml version="1.0" encoding="UTF-8" ?>
<OpenSimDocument Version="40000">
    <IMUInverseKinematicsTool>
        <model_file_name>Rajagopal_calibrated.osim</model_file_name>
        <orientations_file_name>synchronized_kinematics.sto</orientations_file_name>
        <sensor_to_opensim_rotations>-1.57079632679 0 0</sensor_to_opensim_rotations>
        <time_range>0 {MAX_TIME_S}</time_range>
        <results_directory>IKResults</results_directory>
    </IMUInverseKinematicsTool>
</OpenSimDocument>
```

---

## 6. Android Implementation & Architecture Stack

### Recommended Stack:
- **Language**: Kotlin 2.x
- **UI Framework**: Jetpack Compose (Material Design 3)
- **Concurrency / Async**: Kotlin Coroutines & StateFlow
- **Target SDK**: `compileSdk 35`, `targetSdk 35`, `minSdk 26` (Android 8.0+)
- **MetaWear Library**: MbientLab official Kotlin SDK:
  `mbientlab/MetaWear-API-Kotlin` (`:metawear-protocol` + `:metawear-core`)
- **Bluetooth Permissions**:
  - `android.permission.BLUETOOTH_SCAN` (`android:usesPermissionFlags="neverForLocation"`)
  - `android.permission.BLUETOOTH_CONNECT`
  - `android.permission.FOREGROUND_SERVICE`
  - `android.permission.FOREGROUND_SERVICE_CONNECTED_DEVICE`
  - `android.permission.POST_NOTIFICATIONS`

### Background Execution Strategy
Multi-sensor download takes 2 to 4 minutes. To prevent Android Doze Mode or OS kill signals from terminating the download while the phone screen is off or in a pocket:
1. Start an ongoing **Foreground Service** (`Service.startForeground()`) with a sticky notification displaying live download progress (`[3/7] Downloading femur_r_imu: 45%`).
2. Keep `WakeLock` or `FLAG_KEEP_SCREEN_ON` active during the live trial and download phases.

### Cloud Export Strategy
Avoid OAuth credentials or cloud API keys.
1. Save all generated trial files into app-external files directory:
   `/storage/emulated/0/Android/data/<package>/files/OpenSim_Trials/<trial_name>/`
2. Bundle files or provide multi-file `Uri` array using Android `FileProvider`.
3. Launch `Intent.createChooser` with `Intent.ACTION_SEND_MULTIPLE`.
4. Researcher selects Google Drive, OneDrive, or local Files app to export.

---

## 7. Reference Test Package & Verification Benchmarks

A verified reference package has been created in the `metawear-research` repository under:
`data/android_reference_package/`

### Package Contents:
```text
data/android_reference_package/
├── raw_inputs/
│   ├── torso_imu_accel.csv          # Raw acceleration from 30s walking trial
│   ├── torso_imu_quat.csv           # Raw quaternions
│   ├── femur_r_imu_accel.csv        # Raw acceleration
│   └── femur_r_imu_quat.csv         # Raw quaternions
├── ground_truth_outputs/
│   ├── synchronized_kinematics.sto  # Exact 100 Hz OpenSense output
│   ├── setup_imu_placer.xml         # Generated Placer XML
│   ├── setup_imu_ik.xml             # Generated IK XML
│   ├── torso_imu_quat_synced.csv    # Trimmed & t0-aligned intermediate
│   ├── femur_r_imu_quat_synced.csv  # Trimmed & t0-aligned intermediate
│   └── *_sync_plot.png              # Detection verification plots
└── config/
    └── sensors_config.json          # Lab sensor MAC mapping and parameters
```

### Numerical Ground-Truth Benchmarks for Android Unit Tests:
When testing the native Kotlin math algorithms against `raw_inputs/`:
- **Static Window**: first 50 samples
- **Std Factor**: 5.0
- **`torso_imu` Detected Sync $t_0$**: `1785956091016` ms
- **`femur_r_imu` Detected Sync $t_0$**: `1785956091051` ms
- **Common Duration**: `32.79` seconds
- **Output STO Frames**: Exactly `3280` frames at `100.0 Hz`
- **Output Quality Check**: Zero `NaN` values, all quaternions $\|q\| = 1.0 \pm 10^{-6}$.
