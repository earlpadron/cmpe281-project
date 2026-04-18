# Intelligent Edge-Cloud Task Placement Framework

This project implements a hybrid edge-cloud serverless framework that dynamically routes image processing tasks between a Raspberry Pi (Edge) and AWS Lambda (Cloud) based on Machine Learning predictions.

## Architecture Overview
The system relies on an Intelligent Edge Node (Raspberry Pi) to intercept user image uploads. It extracts hardware and network telemetry in real-time and uses ML inference (Ridge Regression & GBRT) to decide the optimal placement for the heavy image resizing task:
1.  Edge Execution: Processed locally on the ARM CPU.
2.  Cloud Execution: Uploaded to Amazon S3 and processed by AWS Lambda.

---

## Project Structure
The repository is organized by system components:

```text
cmpe281-project/
├── backend/                  # FastAPI Application (Runs on Raspberry Pi)
│   ├── main.py               # Core Decision Engine & Routing Logic
│   └── models/               # Serialized ML models (.pkl)
├── cloud/                    # AWS Serverless Code
│   └── lambda_function.py    # AWS Lambda image resizer
├── frontend/                 # Web User Interface
│   └── index.html            # Static UI (Hosted on S3 or opened locally)
├── scripts/                  # Data Collection & MLOps
│   ├── benchmark.py          # Generates empirical edge/cloud latency datasets
│   ├── train_models.py       # Trains and exports the ML models
│   └── download_dataset.py   # Downloads the Unsplash image dataset
├── infrastructure/           # AWS Identity & Security Policies (IaC)
│   ├── s3-policy.json
│   ├── trust-policy.json
│   └── frontend-bucket-policy.json
├── docs/                     # Comprehensive Guides & Architecture Documents
└── images/                   # Local dataset directory (Not tracked in Git)
```

---

## Prerequisites & Setup

1. Python 3.10+
2. AWS CLI configured via `aws configure` (Ensure you have access to the shared group account).
3. Install Dependencies:
   ```bash
   pip install boto3 pillow psutil pandas tqdm requests "fastapi[standard]" uvicorn scikit-learn joblib
   ```

---

## Part 1: Running the Full Application (Local Testing)

You can run the entire Edge-Cloud architecture locally on your machine to test the integration between the Frontend UI, the ML Decision Engine, and the AWS Cloud.

### 1. Start the Backend (Edge Decision Engine)
Navigate to the `backend/` directory and start the FastAPI server:
```bash
cd backend
uvicorn main:app --reload
```
*The server will start on `http://127.0.0.1:8000` and load the `.pkl` ML models into memory.*

### 2. Open the Frontend UI
1. Open a new terminal window.
2. Navigate to the `frontend/` directory.
3. Simply double-click `index.html` to open it in your web browser (Chrome, Firefox, Safari).

### 3. Test the Routing
1. Drag and drop a large image into the browser UI.
2. Click "Resize images".
3. Watch the Backend Terminal: You will see the system dynamically decide to route to EDGE or CLOUD based on the ML latency/cost predictions.
4. Watch the UI: The resized image will either download directly (Edge) or provide a secure, 5-minute S3 Presigned URL link (Cloud) to download the processed thumbnail from the private Data Lake.

---

## Part 2: Benchmarking & ML Training (MLOps)

If you want to gather new latency data from the Raspberry Pi or retrain the Machine Learning models, follow these steps.

### 1. Download the Raw Dataset
We use the Unsplash Lite Dataset to test real-world image entropy.
1. Download the Unsplash Lite Dataset (photos.csv000) and place it in the root folder.
2. Run the downloader to fetch 1,000(OR MORE, this param can be changed) images into the `images/` directory:
```bash
cd scripts
python3 download_dataset.py
```

### 2. Run the Benchmark (Data Collection)
This script processes the images on both the local CPU and AWS Lambda to generate an empirical CSV dataset of latency, hardware utilization, and cloud cost.
```bash
cd scripts
python3 benchmark.py
```
*(A `benchmark_results_[TIMESTAMP].csv` file will be generated in the root directory).*

### 3. Train the Models
Train the Ridge Regression and GBRT models using the new CSV data:
1. Open `scripts/train_models.py` and ensure `LATEST_CSV` points to your newly generated file.
2. Run the training script:
```bash
cd scripts
python3 train_models.py
```
*This will output new `.pkl` files directly into the `backend/models/` directory, automatically updating the Decision Engine.*

---

## Part 3: AWS Infrastructure & Deployment

Detailed guides for setting up the cloud infrastructure are located in the `docs/` folder:

*   `docs/SHARED_ACCOUNT_SETUP_GUIDE.md`: Instructions for the AWS Admin to create the S3 buckets and IAM Roles.
*   `docs/AWS_SETUP_GUIDE.md`: Explains how the CLI deployment maps to the AWS Console.
*   `docs/PI_SETUP_GUIDE.md`: Instructions for preparing the physical Raspberry Pi environment.
*   `docs/design-doc.md`: The Master's-level Software Design Document explaining the architectural rationale, S3 pointer patterns, and IoT Core limitations.

---

## Authors
- Bryan Cortes
- Earl Padron
- Irwin Salamanca
- Matthew Tang