# Intelligent Edge-Cloud Task Placement Framework

This project implements a hybrid edge-cloud serverless framework that dynamically routes image processing tasks between a Raspberry Pi (Edge) and AWS Lambda (Cloud) based on Machine Learning predictions.

## Architecture Overview
An Intelligent Edge Node (Raspberry Pi) intercepts every user image upload, samples hardware and network telemetry in real time, and consults two ML models (Ridge Regression for latency, GBRT for cloud cost) to pick the optimal placement for the heavy image-resizing task:

1.  **Edge Execution** — handled by the `cmpe281.edge_resizer` AWS IoT Greengrass v2 component running on the Pi's ARM CPU.
2.  **Cloud Execution** — image is uploaded to Amazon S3 and processed by AWS Lambda; the Pi receives a presigned URL pointer instead of the bytes.

Three design choices anchor the philosophy of this codebase:

*   **Greengrass-managed edge.** In production the EDGE pipeline runs as two Greengrass v2 components (`cmpe281.decision_engine` for routing, `cmpe281.edge_resizer` for Pillow work) wired together by local pub/sub IPC. This gives us managed lifecycle, OTA deployment, IAM via TES, and resource isolation between the API and the CPU-hot resize loop.
*   **Dev/prod parity.** The exact same `backend/main.py` runs under plain `uvicorn` for local development *and* under Greengrass in production. A single env var (`USE_GREENGRASS_IPC`) flips between in-process Pillow and IPC-delegated resize — there are no separate code paths to keep in sync.
*   **Byte-identical EDGE and CLOUD output.** A single shared `backend/lib/resize.py` module is packaged into the Lambda zip *and* the Greengrass component artifact (as `resize_lib.py`), so EDGE and CLOUD always produce the same thumbnail for the same input.

---

## Project Structure
The repository is organized by system components:

```text
cmpe281-project/
├── backend/                  # FastAPI Application (Runs on Raspberry Pi)
│   ├── main.py               # Core Decision Engine & Routing Logic
│   ├── ipc_client.py         # Greengrass IPC bridge to cmpe281.edge_resizer
│   ├── lib/resize.py         # Shared resize core (used by Lambda + Greengrass)
│   └── models/               # Serialized ML models (.pkl)
├── cloud/                    # AWS Serverless Code
│   └── lambda_function.py    # AWS Lambda image resizer (imports resize_lib)
├── frontend/                 # Web User Interface
│   └── index.html            # Static UI (Hosted on S3 or opened locally)
├── scripts/                  # Data Collection & MLOps
│   ├── benchmark.py          # Generates empirical edge/cloud latency datasets
│   ├── train_models.py       # Trains and exports the ML models
│   └── download_dataset.py   # Downloads the Unsplash image dataset
├── greengrass/               # AWS IoT Greengrass v2 deployment artifacts
│   ├── components/cmpe281.decision_engine/  # Edge Decision Engine (FastAPI) as a managed component
│   ├── components/cmpe281.edge_resizer/     # Pillow resize worker as a managed component
│   ├── infra/                # TES role IAM policies
│   └── scripts/              # bootstrap_pi.sh, publish_component.sh, deploy.sh
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
3. Install dependencies (single source of truth: `backend/requirements.txt`):
   ```bash
   pip install -r backend/requirements.txt
   # Plus the MLOps-only deps used by scripts/ (not needed at request-time):
   pip install tqdm requests
   ```
   *`awsiotsdk` is in `backend/requirements.txt` so the same install works for both local-dev (in-process resize) and the Greengrass-IPC path.*

---

## Part 1: Running the Full Application (Local Testing)

You can run the entire Edge-Cloud architecture locally on your machine to test the integration between the Frontend UI, the ML Decision Engine, and the AWS Cloud.

### 1. Start the Backend (Edge Decision Engine)
Navigate to the `backend/` directory and start the FastAPI server:
```bash
cd backend
uvicorn main:app --reload
```
*The server will start on `http://127.0.0.1:8000` and load the `.pkl` ML models into memory. Local-dev mode runs with `USE_GREENGRASS_IPC=false` (the default), so `curl http://127.0.0.1:8000/health` will report `"edge_backend":"in-process"` — that's expected. The Greengrass-managed `"edge_backend":"greengrass"` path is covered in Part 4.*

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
*   `docs/PI_SETUP_GUIDE.md`: Instructions for preparing the physical Raspberry Pi environment, **including the optional AWS IoT Greengrass v2 deployment** (Section 6).
*   `docs/design-doc.md`: The Master's-level Software Design Document explaining the architectural rationale, S3 pointer patterns, and IoT Core limitations.

### Shared resize library (Lambda packaging)
`cloud/lambda_function.py` does not contain the resize logic itself — it imports
the shared core via `from resize_lib import resize_bytes`. When you build the
Lambda deployment zip, copy `backend/lib/resize.py` in alongside
`lambda_function.py` as a sibling module named `resize_lib.py`:

```bash
mkdir -p build/lambda
cp cloud/lambda_function.py   build/lambda/
cp backend/lib/resize.py      build/lambda/resize_lib.py
( cd build/lambda && zip -r ../lambda.zip . )
aws lambda update-function-code \
    --function-name cmpe281-shared-image-resizer \
    --zip-file fileb://build/lambda.zip
```

The Greengrass publish script (`greengrass/scripts/publish_component.sh`) does
the equivalent copy automatically when bundling `cmpe281.edge_resizer`, so EDGE
and CLOUD always run the same `resize_bytes()` implementation. (`SHARED_ACCOUNT_SETUP_GUIDE.md` documents the Lambda packaging step in full.)

---

## Part 4: AWS IoT Greengrass v2 Deployment (Production Edge)

In production, the EDGE pipeline runs as two managed Greengrass v2 components:

*   **`cmpe281.decision_engine`** — Edge Decision Engine. Same FastAPI code as
    `backend/main.py`, but launched with `USE_GREENGRASS_IPC=true` so the EDGE
    path delegates resizing over local pub/sub instead of running Pillow
    in-process.
*   **`cmpe281.edge_resizer`** — Long-running Pillow worker that subscribes to
    `cmpe281/edge/resize/request`. Uses the *same* `resize_lib.py` module that
    the cloud Lambda imports — so EDGE and CLOUD produce byte-identical output.

Bytes are exchanged through `/tmp/cmpe281/<uuid>.{in,out}.<ext>` so multi-MB
images travel cleanly without bumping into IPC payload limits.

> **Note on the imported `cmpe281-image-resizer` Lambda component.** If you
> imported the cloud Lambda (`cmpe281-image-resizer` / `cmpe281-shared-image-resizer`)
> into Greengrass v2 from the AWS console, that component is **independent of
> the local edge worker built here**. It can stay imported (it does no harm),
> but our EDGE path runs through the native Python `cmpe281.edge_resizer`
> component for lower overhead. The CLOUD path still invokes the AWS-hosted
> Lambda via `boto3` regardless of whether it's also a Greengrass component.

### 4.1 Quickstart — fresh setup (Pi has nothing yet)

```bash
# 1. Provision the TES role (admin, once)
aws iam create-role --role-name GreengrassV2TokenExchangeRole \
    --assume-role-policy-document file://greengrass/infra/tes-trust-policy.json
aws iam put-role-policy --role-name GreengrassV2TokenExchangeRole \
    --policy-name GreengrassV2TokenExchangeRoleAccess \
    --policy-document file://greengrass/infra/tes-permission-policy.json

# 2. Bootstrap the Pi (on the Pi, once)
greengrass/scripts/bootstrap_pi.sh cmpe281-pi-01 cmpe281-edge-fleet

# 3. Publish components (on dev machine)
export ARTIFACT_BUCKET=cmpe281-greengrass-artifacts
greengrass/scripts/publish_component.sh cmpe281.edge_resizer    1.0.0
greengrass/scripts/publish_component.sh cmpe281.decision_engine 1.0.0

# 4. Deploy to the Pi fleet
greengrass/scripts/deploy.sh cmpe281-edge-fleet
```

### 4.2 Runbook — Greengrass already on the Pi, Lambda already imported

If your starting state is:

*   Greengrass v2 nucleus is already installed and `sudo systemctl status greengrass` shows it running on the Pi.
*   The cloud Lambda has already been imported into Greengrass v2 as a component (e.g. `cmpe281-image-resizer`).

…then you can **skip `bootstrap_pi.sh`** and only need the four steps below.

#### Step 1 — Find your existing Pi's Thing name and Thing Group
The bootstrap script normally creates these for you. Since Greengrass is already provisioned, look up the names that were used:

```bash
aws greengrassv2 list-core-devices --region us-east-1
# -> note the coreDeviceThingName, e.g. "cmpe281-pi-01"

aws iot list-thing-groups-for-thing --thing-name cmpe281-pi-01 --region us-east-1
# -> note the group name, e.g. "cmpe281-edge-fleet"
# If the Pi isn't in a group yet, add it:
#   aws iot create-thing-group --thing-group-name cmpe281-edge-fleet
#   aws iot add-thing-to-thing-group \
#       --thing-name cmpe281-pi-01 --thing-group-name cmpe281-edge-fleet
```

#### Step 2 — Verify the Pi's TES role has the permissions our components need
Greengrass auto-created a Token Exchange Service role at install time (often called `GreengrassV2TokenExchangeRole` or similar). It needs to be able to read our artifact bucket, read/write the data-lake bucket, and pull the `.pkl` models. Apply our permission policy:

```bash
# Replace __ARTIFACT_BUCKET__ in the JSON with your real bucket name first.
sed -i.bak "s/__ARTIFACT_BUCKET__/cmpe281-greengrass-artifacts/g" \
    greengrass/infra/tes-permission-policy.json

# Find the role attached to your Greengrass core
ROLE_NAME=$(aws iot list-role-aliases --query 'roleAliases[?contains(@, `Greengrass`)] | [0]' --output text \
    | xargs -I {} aws iot describe-role-alias --role-alias {} \
    --query 'roleAliasDescription.roleArn' --output text \
    | awk -F'/' '{print $NF}')

aws iam put-role-policy \
    --role-name "${ROLE_NAME}" \
    --policy-name CMPE281EdgeAccess \
    --policy-document file://greengrass/infra/tes-permission-policy.json
```

#### Step 3 — Create an artifact bucket (skip if you already have one)
```bash
aws s3 mb s3://cmpe281-greengrass-artifacts --region us-east-1
```

#### Step 4 — Publish both components and deploy
```bash
export ARTIFACT_BUCKET=cmpe281-greengrass-artifacts
export AWS_REGION=us-east-1

greengrass/scripts/publish_component.sh cmpe281.edge_resizer    1.0.0
greengrass/scripts/publish_component.sh cmpe281.decision_engine 1.0.0

# Use the Thing Group name you confirmed in Step 1
greengrass/scripts/deploy.sh cmpe281-edge-fleet
```

#### Step 5 — Verify processing is happening inside Greengrass
On the Pi:
```bash
# Both components should be RUNNING
sudo /greengrass/v2/bin/greengrass-cli component list

# Live tail both component logs
sudo tail -f /greengrass/v2/logs/cmpe281.decision_engine.log /greengrass/v2/logs/cmpe281.edge_resizer.log
```

From any machine on the LAN:
```bash
curl http://<pi-ip>:8000/health
# Expect: {"status":"ok","edge_backend":"greengrass","models_loaded":true,...}
```

End-to-end: open the frontend, drop a small image. In the response headers
you'll see:
- `X-Routing-Decision: EDGE` (ML chose local processing)
- `X-Edge-Backend: greengrass` (proves the resize ran inside
  `cmpe281.edge_resizer`, not in-process)

In `cmpe281.edge_resizer.log` you should see a matching `Resizing request_id=…`
line for every EDGE-routed image — that's the conclusive proof that processing
is happening inside Greengrass.

> **Local development is unchanged.** When `USE_GREENGRASS_IPC` is unset (or
> `false`), `backend/main.py` runs the resize in-process exactly as before, so
> `uvicorn main:app --reload` on a laptop still works without Greengrass.

---

## Authors
- Bryan Cortes
- Earl Padron
- Irwin Salamanca
- Matthew Tang