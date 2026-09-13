# MetaMotion to OpenSim Data Pipeline

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![OpenSim 4.0+](https://img.shields.io/badge/OpenSim-4.0+-brightgreen.svg)](https://simtk.org/projects/opensim)
[![MbientLab MetaMotion](https://img.shields.io/badge/Hardware-MbientLab%20MMS-orange.svg)](https://mbientlab.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An end-to-end research pipeline for multi-sensor biomechanical motion capture using MbientLab MetaMotion (MMS) IMUs and OpenSim / OpenSense.

This system coordinates multi-sensor Bluetooth Low Energy (BLE) arming, high-frequency onboard flash logging, sequential fault-tolerant downloads, kinematic synchronization, and automated generation of OpenSense `.sto` orientation files and OpenSim setup XMLs.

---

## 🏛️ System Architecture

To minimize friction for lab members and eliminate Bluetooth driver/compilation issues on student laptops, this project employs a **Dedicated Linux Acquisition Hub** model:

```
                  ┌───────────────────────────────────────────────┐
                  │    LAB ACQUISITION HUB (Dedicated Linux PC)   │
                  │   - BLE Hardware Control & Flash Logging      │
                  │   - Fault-tolerant Download & Initial Sync    │
                  └───────────────────────┬───────────────────────┘
                                          │ Raw CSVs & OpenSim Files
                                          │ (via USB Drive, Cloud, or Git)
                                          ▼
                  ┌───────────────────────────────────────────────┐
                  │      ANALYSIS WORKSTATIONS (Windows / Mac)    │
                  │   - Offline Processing: python cli.py process │
                  │   - OpenSense Biomechanical Modeling (GUI)    │
                  │   - OpenSim Inverse Kinematics (IK) Analysis  │
                  └───────────────────────────────────────────────┘
```

- **Data Acquisition (Linux Hub)**: Communicates directly over BLE using BlueZ and the MbientLab C++ wrapper to arm sensors, stream/log data, and download flash recordings.
- **Data Processing (Cross-Platform)**: Pure Python (`pandas`, `numpy`, `scipy`, `matplotlib`) runs identically on Windows, Linux, and macOS to synchronize data streams, generate OpenSense `.sto` files, and construct OpenSim XML files.

---

## 📁 Repository Structure

```text
metawear-research/
├── config/
│   └── sensors_config.json          # Sensor MAC addresses, segment mappings, & fusion config
├── data/
│   └── sample_trials/               # Validated sample trial datasets for offline testing
│       └── default_two_sensor/      # 2-sensor validation data (torso_imu, femur_r_imu)
├── legacy/                          # Historical prototyping and single-sensor exploratory scripts
│   └── opensense_prototypes/
├── pipeline/
│   ├── __init__.py
│   ├── cli.py                       # Unified command-line interface
│   └── core/
│       ├── __init__.py
│       ├── board_manager.py         # BLE connection, battery monitoring, arming, download, wipe
│       ├── sync.py                  # Motion onset detection, static calibration, & time alignment
│       ├── formatter.py             # OpenSense .sto format writer (unit quaternions, zero NaNs)
│       └── xml_builder.py           # Generates setup_imu_placer.xml and setup_imu_ik.xml
├── scripts/
│   └── setup_permissions.sh         # Linux non-root Bluetooth capabilities setup (no sudo needed)
├── tests/
│   └── test_pipeline_offline.py     # Offline unit and integration verification test suite
├── .gitignore
├── requirements.txt                 # Dual-platform dependencies (with PEP 508 environment markers)
└── README.md
```

---

## 🏷️ Configured Sensor Layout

Sensors are mapped to anatomical segments in [`config/sensors_config.json`](config/sensors_config.json):

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
- **Sensor Fusion Mode**: `IMUPlus` (6-axis on-board sensor fusion: Accelerometer + Gyroscope, immune to magnetic distortion).
- **Sampling Frequency**: `100.0 Hz`.
- **Accelerometer Range**: `±16.0 g`.
- **Gyroscope Range**: `±2000.0 DPS`.

---

## 🚀 Getting Started

### 1. Dedicated Linux Acquisition Hub Setup

On the lab's Linux machine (Ubuntu 20.04/22.04/Debian recommended with Python 3.9):

```bash
# 1. Install system Bluetooth and build packages
sudo apt update
sudo apt install -y bluetooth bluez libbluetooth-dev libudev-dev libboost-all-dev build-essential git

# 2. Clone the repository
git clone <YOUR_GITHUB_REPO_URL>
cd metawear-research

# 3. Create and activate a Python 3.9 virtual environment
python3.9 -m venv metawear_39_env
source metawear_39_env/bin/activate

# 4. Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 5. Grant non-root BLE access (run once)
chmod +x scripts/setup_permissions.sh
./scripts/setup_permissions.sh
```

> **Why `setup_permissions.sh`?** Linux requires special raw socket permissions (`cap_net_raw,cap_net_admin`) to scan and connect over BLE. This script grants those capabilities directly to your virtual environment's Python binary, allowing you to run all commands **without `sudo`**!

---

### 2. Windows / Mac Analysis Workstation Setup

For lab members doing analysis or OpenSim modeling on Windows or macOS:

```bash
# 1. Clone repository
git clone <YOUR_GITHUB_REPO_URL>
cd metawear-research

# 2. Create and activate virtual environment
python -m venv venv
# On Windows:
venv\Scripts\activate
# On Mac/Linux:
source venv/bin/activate

# 3. Install offline processing requirements
pip install -r requirements.txt

# 4. Verify offline pipeline
pytest tests/test_pipeline_offline.py
```

*Note: On Windows/macOS, `requirements.txt` automatically skips the Linux-only `metawear` and `warble` packages using PEP 508 platform markers, ensuring clean installation without C++ build tool errors.*

---

## 💻 CLI Commands & Lab Usage

All operations are controlled through `pipeline/cli.py`:

### 1. Check Configured Sensors
```bash
python pipeline/cli.py list
```
Displays all enabled and disabled sensors, their anatomical segments, MAC addresses, and active fusion settings.

### 2. Diagnostic Connection & Blink Test
```bash
python pipeline/cli.py test --blink-time 2.5
```
Sequentially connects to each enabled sensor, checks battery voltage and percentage, and blinks the green onboard LED for 2.5 seconds. Use this before every trial to verify all sensors are charged, responsive, and properly identified.

### 3. Record a Motion Trial
```bash
# Record for 30 seconds (default)
python pipeline/cli.py record --duration 30.0 --name subject01_walking01

# Record and auto-copy results to a plugged-in USB flash drive
python pipeline/cli.py record --duration 45.0 --name subject01_running01 --usb
```
**What happens during `record`:**
1. Connects to all sensors sequentially and programs them for synchronized onboard flash logging.
2. Arms sensors into logging mode and initiates trial countdown.
3. Once trial completes, sequentially reconnects and downloads flash memory over high-speed BLE.
4. Saves raw timestamped CSVs (`*_accel.csv` and `*_quat.csv`).
5. Runs time-alignment and static-window calibration to synchronize all sensors to a common \(t_0\).
6. Outputs OpenSense-compatible `synchronized_kinematics.sto`.
7. Generates OpenSim `setup_imu_placer.xml` and `setup_imu_ik.xml`.
8. If `--usb` is specified, copies trial files directly to `OpenSim_Trials/<trial_name>` on the detected USB drive!

### 4. Reprocess Existing Raw Data
```bash
python pipeline/cli.py process --input-dir output/subject01_walking01
```
Re-runs synchronization, STO formatting, and XML generation on any directory containing raw CSVs without needing physical sensors connected. (Works on Windows, Mac, and Linux!)

### 5. Factory Wipe & Board Reset
```bash
python pipeline/cli.py wipe
```
Clears onboard flash logs, removes persistent macros, and performs a soft reset on all sensors. Run this if a sensor's memory becomes full or if a trial was interrupted.

---

## 🦴 OpenSense & OpenSim Modeling Workflow

Once files are generated (`synchronized_kinematics.sto`, `setup_imu_placer.xml`, `setup_imu_ik.xml`), you can immediately run inverse kinematics in OpenSim on Windows:

### Step 1: Open OpenSim GUI
1. Launch **OpenSim 4.x** on your workstation.
2. Open the baseline musculoskeletal model: `File -> Open Model -> Rajagopal_2015.osim`.

### Step 2: IMU Placer (Model Calibration)
1. In OpenSim, navigate to: `Tools -> IMU Placer`.
2. Click `Load Parameters` and select the generated `setup_imu_placer.xml`.
3. Verify that the orientation file is pointing to `synchronized_kinematics.sto`.
4. Ensure the calibration time range covers the initial static standing pose (typically `0.0` to `2.0` seconds).
5. Click **Apply**. OpenSim will align virtual IMUs on the model bodies with the experimental data and output `Rajagopal_calibrated.osim`.

### Step 3: Inverse Kinematics (IK)
1. Navigate to: `Tools -> Inverse Kinematics`.
2. Click `Load Parameters` and select `setup_imu_ik.xml`.
3. Ensure the model is set to `Rajagopal_calibrated.osim` and the experimental orientation file is set to `synchronized_kinematics.sto`.
4. Click **Run**.
5. The model will animate the recorded movement, and joint angles (hip flexion, knee flexion, ankle dorsiflexion, pelvis tilt, etc.) will be exported to a `.mot` motion file!

---

## 🔧 Maintenance & Troubleshooting

### Adapter Conflict (`hci0` vs `hci1`)
If using an external long-range USB Bluetooth dongle alongside an internal motherboard Bluetooth chip:
- The pipeline automatically detects the active default adapter via `detect_active_hci_mac()`.
- You can override the adapter MAC manually in `config/sensors_config.json` under `"hci_mac": "XX:XX:XX:XX:XX:XX"`.

### Sensor Charging vs BLE Mode
When MetaMotion sensors are plugged into a computer USB port to charge, the SDK may try to communicate via USB serial instead of BLE. The pipeline automatically forces BLE mode:
```python
type(device.usb).is_enumerated = property(lambda self: False)
```
Always verify sensors have at least 20% battery charge before starting experimental trials.

### Handling Connection Timeouts
If a sensor is out of range or sleeping:
- Press the physical push-button on the sensor to wake it from deep sleep.
- The pipeline automatically attempts up to 3 retries with exponential backoff for every sensor operation.
- If a sensor remains unresponsive, run `python pipeline/cli.py test` to isolate which sensor is offline.

---

## 📄 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
