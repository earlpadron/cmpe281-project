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
