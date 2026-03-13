# Software Design Document (SDD): Intelligent Edge-Cloud Task Placement Framework

## 1. Project Context & Objectives
**Goal:** Implement a hybrid edge-cloud serverless framework for image resizing that dynamically routes tasks based on machine learning predictions of execution latency and cost. 
**Constraint Level:** Master's-level Software Engineering project. Code must adhere to strict enterprise standards, emphasizing latency optimization, modularity, and comprehensive error handling.
**Core Architecture:**
* **Edge Node:** Raspberry Pi running AWS IoT Greengrass.
* **Cloud Compute:** AWS Lambda (Function-as-a-Service).
* **Storage:** Amazon S3.
* **Backend:** Python with FastAPI.
* **Control Plane:** AWS IoT Core (MQTT).
* **Monitoring:** Amazon CloudWatch.

## 2. Implementation Phases & Directives

### Phase 1: Data Collection & Benchmarking (Current Focus)
**Objective:** Generate a custom, empirical dataset by benchmarking an image processing workload across both the edge device and the cloud.
**Dataset Source:** The Unsplash High-Quality Image Dataset (Kaggle). *Directive: Do not write code for synthetic image generation. The script must iterate through a local directory of real high-resolution JPEG/PNG files.*
**Requirements for the Python Benchmarking Script (`benchmark.py`):**
* Execute locally on the Raspberry Pi.
* Use `Pillow` (PIL) for the edge image resizing workload.
* Use `boto3` to interact with AWS (S3 uploads, Lambda invocation, CloudWatch logs).
* Use `psutil` to capture edge CPU (%) and Memory (%) utilization.
* Extract features: Image size (bytes), file format, network RTT, estimated uplink bandwidth.
* Extract target variables: Edge total latency (ms), Cloud total latency (ms), estimated cloud cost (USD).
* Cloud Cost Calculation: Must be calculated programmatically using the AWS x86 pricing formula and the `Billed Duration` extracted from CloudWatch logs.
* Output: A clean Pandas DataFrame exported to a `.csv` file.

### Phase 2: Machine Learning Pipeline
**Objective:** Train supervised regression models to predict execution metrics.
**Requirements:**
* **Data Split:** Chronological split to prevent temporal network leakage.
* **Edge Predictor:** Ridge Regression (to map linear edge compute scaling).
* **Cloud Predictor:** Gradient Boosted Regression Trees (GBRT) to map non-linear Lambda memory tiers and cold starts.
* **Serialization:** Export trained models as `.pkl` or `.joblib` objects for deployment to the edge.

### Phase 3: Edge Decision Engine & Routing Policy
**Objective:** The real-time AI inference and routing logic hosted on the Raspberry Pi via FastAPI.
**Routing Logic (Strict Order of Operations):**
1.  **Concurrency Hard Limit (User Activity):** Track active queued tasks. If the active queue exceeds the Pi's processing threshold, instantly route to AWS Lambda to prevent bottlenecks.
2.  **ML Inference:** If the edge is free, run task features through the Ridge and GBRT models.
3.  **Optimization Policy:** Route to the cloud ONLY IF `Predicted_Cloud_Latency < Predicted_Edge_Latency` AND `Predicted_Cloud_Cost <= User_Budget`. Otherwise, route to the edge.
4.  **Proactive Cold Start Mitigation:** If the decision is to route to the Edge, the FastAPI backend must simultaneously fire a lightweight, asynchronous "Warming Ping" to AWS Lambda to initialize the cloud container for future concurrent users.

## 3. Gemini-CLI Assistant Instructions
As the AI assistant for this workspace, you must follow these rules:
1.  **Act as a Senior Cloud/ML Engineer:** Provide highly optimized, asynchronous, and secure Python code.
2.  **AWS Best Practices:** Use `boto3` efficiently. Ensure all IAM permissions and security boundaries (TLS 1.2+, SSE-S3) are respected in the code logic.
3.  **Step-by-Step Execution:** Do not write the entire system at once. We will begin exclusively with Phase 1 (The Benchmarking Script). Await explicit user prompts before moving to the next phase.