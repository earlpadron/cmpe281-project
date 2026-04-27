# Raspberry Pi: Edge Environment Setup Guide

This guide provides the exact steps to prepare your Raspberry Pi to run the `benchmark.py` script. **Collecting data on the Pi is mandatory** to ensure the ML models accurately reflect the Edge's performance.

---

## 1. System Dependencies
The Raspberry Pi (Raspbian/Debian) requires system-level libraries to handle image processing and high-performance math. Run these commands first:

```bash
sudo apt-get update
sudo apt-get install -y \
    python3-pip \
    python3-venv \
    libjpeg-dev \
    zlib1g-dev \
    libatlas-base-dev \
    libopenjp2-7 \
    libtiff5
```

---

## 2. Project Setup
We recommend using a Virtual Environment (`venv`) to keep the Pi's system Python clean.

1. **Clone/Copy the Project:**
   Transfer your code and the `images/` folder to the Pi (via USB, Git, or `scp`).

2. **Create the Environment:**
   ```bash
   cd cmpe281-project
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install Python Libraries:**
   *Note: Installing `pandas` on a Pi can take 5-10 minutes as it may compile from source.*
   ```bash
   pip install --upgrade pip
   pip install boto3 Pillow psutil pandas tqdm requests
   ```

---

## 3. AWS Configuration
The Pi needs permission to talk to your S3 bucket and Lambda function.

1. **Install AWS CLI on Pi:**
   ```bash
   sudo apt-get install -y awscli
   ```

2. **Configure Credentials:**
   Run `aws configure` and enter the **Access Key** and **Secret Key** (use the shared group account credentials if available).

---

## 4. Running the Benchmark
Because the Pi's CPU is significantly slower than an M4 Mac, we recommend running the benchmark in the background or using `screen`/`tmux` if you are connecting via SSH.

1. **Start the Benchmark:**
   ```bash
   cd scripts
   python3 benchmark.py
   ```

2. **Performance Expectation:**
   - **M4 Mac:** ~30ms per image.
   - **Raspberry Pi:** ~400ms to 900ms per image.
   - **Total Time (1,000 images):** Approximately 1.5 to 2 hours.

---

## 5. Retrieving Results
Once the script finishes, it will save a `.csv` file. You can move this file back to your Mac for ML training:

```bash
# Example using scp from your Mac terminal:
scp pi@<pi-ip-address>:~/cmpe281-project/benchmark_results_*.csv ./
```

---

### Important Hardware Note
Ensure your Raspberry Pi has adequate cooling (heatsink or fan). Running 1,000 consecutive image resizes will put a sustained 100% load on the CPU, which can cause **Thermal Throttling**. If the Pi gets too hot, it will slow down, and your latency data will become inconsistent!

---

## 6. (Optional) Run the Edge Pipeline as AWS IoT Greengrass v2 Components

Sections 1–5 cover the *manual* deployment where you SSH into the Pi and run
`uvicorn` by hand. For a production-grade setup, the EDGE pipeline can be
managed by **AWS IoT Greengrass v2** instead. The image resize is split into
its own component so the API stays responsive even while resizes are in flight.

### Architecture
| Component | Role | Source |
|---|---|---|
| `cmpe281.decision_engine` | Edge Decision Engine — FastAPI + ML routing policy | `backend/` |
| `cmpe281.edge_resizer` | Pillow image resizer (subscribes to local IPC topic) | `greengrass/components/cmpe281.edge_resizer/artifacts/worker.py` |

The two components communicate over Greengrass local pub/sub on the topics
`cmpe281/edge/resize/request` and `cmpe281/edge/resize/response`. Image bytes
are exchanged via `/tmp/cmpe281/<request_id>.{in,out}.<ext>` to avoid IPC
payload size limits.

### One-time AWS provisioning (admin)
1. Create an S3 bucket for component artifacts (e.g. `cmpe281-greengrass-artifacts`).
2. Create the Greengrass Token Exchange Service role:
   ```bash
   aws iam create-role \
     --role-name GreengrassV2TokenExchangeRole \
     --assume-role-policy-document file://greengrass/infra/tes-trust-policy.json

   # Edit greengrass/infra/tes-permission-policy.json and replace
   # __ARTIFACT_BUCKET__ with your actual bucket name first.
   aws iam put-role-policy \
     --role-name GreengrassV2TokenExchangeRole \
     --policy-name GreengrassV2TokenExchangeRoleAccess \
     --policy-document file://greengrass/infra/tes-permission-policy.json
   ```

### On the Pi (one-time) 
```bash
cd cmpe281-project/greengrass/scripts
AWS_REGION=us-east-1 ./bootstrap_pi.sh cmpe281-pi-01 cmpe281-edge-fleet
```
This installs the Greengrass v2 nucleus as a systemd service and registers the
Pi as a managed core device.

### Publish components (dev machine)
```bash
export ARTIFACT_BUCKET=cmpe281-greengrass-artifacts
./publish_component.sh cmpe281.edge_resizer    1.0.0
./publish_component.sh cmpe281.decision_engine 1.0.0
```
The publish script copies `backend/lib/resize.py` into each component as
`resize_lib.py` so cloud Lambda and edge Greengrass run identical resize code.

### Deploy
```bash
./deploy.sh cmpe281-edge-fleet
```

### Verify
```bash
ssh pi 'sudo tail -f /greengrass/v2/logs/cmpe281.decision_engine.log /greengrass/v2/logs/cmpe281.edge_resizer.log'
curl http://<pi-ip>:8000/health    # expect "edge_backend": "greengrass"
```
### Check deployment
'''aws greengrassv2 get-deployment \
  --deployment-id 6c651b6b-a963-4e6f-b4ec-8e4f54d1382b \
  --region us-east-1```
