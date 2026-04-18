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

---

## 3. Real-World Architecture & AWS IoT Integration (Educational Primer)

A common point of confusion when developing an Edge Computing framework is understanding how data travels from a remote Web Application (like a user's phone on cellular data) through the public internet and into a physical Raspberry Pi hidden behind a private, restrictive home or factory firewall.

Furthermore, how does the FastAPI application (`main.py`) running on the local Raspberry Pi actually receive these images, and how do AWS IoT services fit into this picture?

This section clarifies the dual-path ingestion architecture used in this project: The **LAN Path** (Local) and the **WAN Path** (Public/Cloud).

### The LAN Path: Direct Local Execution (FastAPI)
During development on a Macbook, or in real-world scenarios like a "Smart Factory" where a technician's iPad is on the **same Wi-Fi network** as the Raspberry Pi, the system utilizes the Local Area Network (LAN) path.

*   **Mechanism:** The Web Application bypasses the cloud entirely. It sends a direct HTTP `POST` request containing the heavy image payload to the Pi's local IP address (e.g., `http://192.168.1.50:8000/resize`).
*   **The Role of `main.py`:** The FastAPI application receives the image bytes, runs the ML routing logic (Phase 3), and executes the resize either locally or by offloading to AWS.

### The WAN Path: Public Execution (AWS IoT & S3 Pointer Pattern)
When the Web Application is on the public internet, it cannot send a direct HTTP request to the Raspberry Pi because the Pi is blocked by a firewall (NAT). We must use AWS IoT to bridge the gap.

**The "Payload Limit" Problem:**
Why not just send the image from the Web App, through AWS IoT Core, down to the Pi?
AWS IoT Core uses **MQTT** (Message Queuing Telemetry Transport). MQTT is an incredibly lightweight protocol designed for tiny sensor data (like temperature readings), not massive high-resolution images. AWS IoT Core enforces a **strict hard limit of 128 KB per message.** Our Unsplash images frequently exceed 2MB.

**The Solution: The "S3 Pointer" Pattern**
To bypass this limitation and cross the firewall, we implement a highly scalable, asynchronous cloud pattern:

1.  **User Uploads to Cloud:** The Web Application does *not* attempt to send the image to the Pi. Instead, it uploads the raw, heavy image directly to an **Amazon S3 Bucket** (e.g., to an `inbox/` prefix).
2.  **The MQTT Signal (Cloud -> Pi):** Once the S3 upload finishes, the Web Application (or an AWS service) publishes a tiny JSON message to AWS IoT Core via MQTT:
    ```json
    {
      "task_id": "12345",
      "s3_raw_image_key": "inbox/image.jpg"
    }
    ```
    *How it crosses the firewall:* The Raspberry Pi maintains a constant, outbound, secure MQTT connection to AWS IoT Core. Because the connection was initiated from *inside* the firewall, the firewall allows AWS IoT Core to push messages back down to the Pi instantly.
3.  **The Edge Decision:**
    *   The Pi receives the tiny MQTT JSON message.
    *   The Pi uses `boto3` to quickly pull the massive image down from S3 into its local memory.
    *   The Pi extracts the features (file size, CPU usage) and runs our ML Decision Engine.
4.  **The Execution:**
    *   **If the Pi decides "EDGE":** It resizes the image locally, uploads the finished thumbnail back to S3 (e.g., `outbox/image.jpg`), and publishes an MQTT message back to the Web App confirming completion.
    *   **If the Pi decides "CLOUD":** It saves bandwidth by *not* uploading anything. It invokes AWS Lambda, tells it to pull the image from the `inbox/` bucket, and then returns a **secure 5-minute Presigned URL** to the Web App so the unauthenticated browser can securely download the processed thumbnail from the private S3 Data Lake.

### The Role of AWS IoT Greengrass
Finally, how do we deploy and run `main.py` on the physical Pi without manually SSHing in and typing `uvicorn main:app`?

**AWS IoT Greengrass** acts as an operating system manager on the Edge device. We package `main.py` into a "Greengrass Component."
*   **Deployment:** We push the component from the AWS Console. Greengrass automatically downloads the Python code to the Pi.
*   **Management:** Greengrass installs the dependencies, starts the FastAPI server automatically when the Pi boots up, restarts it if it crashes, and manages the secure certificates required to talk to AWS IoT Core and S3, ensuring we never have to hardcode AWS API keys onto the physical device.

---

## 4. MLOps Lifecycle: Cloud Training vs. Edge Inference (Educational Primer)

### The Difference Between Training and Inference
In Machine Learning, there are two distinct phases:

1.  **Training (The Heavy Lifting):** This is the process of crunching millions of rows of CSV data to find mathematical patterns. As we discussed, this is what you would do in AWS SageMaker in an enterprise setting because it requires massive compute power and access to huge data lakes (S3/CloudWatch).
2.  **Inference (The Quick Prediction):** Once the model is trained, it is compiled into a highly optimized, serialized object (the `.pkl` file). This file is essentially just a complex mathematical formula. Making a prediction (inference) using a `.pkl` file takes less than a millisecond and requires almost zero compute power.

### Why the Models Must Live on the Raspberry Pi
The entire premise of your "Intelligent Edge-Cloud Task Placement Framework" is to avoid unnecessary cloud latency.

*   **The Flawed Cloud-Inference Approach:** Imagine if we deployed the models to AWS SageMaker. Every time a user uploaded an image to the Raspberry Pi, the Pi would have to send an HTTP request across the internet to SageMaker just to ask, *"Should I process this locally or in the cloud?"* Waiting for that answer would add 50ms - 200ms of Network Round Trip Time (RTT). We would be introducing cloud latency just to decide how to avoid cloud latency!
*   **The Intelligent Edge Approach:** By loading the `.pkl` files directly into the memory of the FastAPI application (`main.py`) running on the Raspberry Pi, the Decision Engine can evaluate the hardware metrics and make a routing decision locally in **~0.5 milliseconds**.

### The Full MLOps Lifecycle for your Architecture
In a production version of your architecture, the workflow looks like this:

1.  **Data Collection:** The Pi processes images and streams latency logs to CloudWatch/S3.
2.  **Cloud Training:** Every week, AWS SageMaker spins up, pulls the massive logs from S3, and trains new, smarter Ridge and GBRT models.
3.  **Model Delivery:** SageMaker exports the new `.pkl` files.
4.  **Edge Update (OTA):** AWS IoT Greengrass securely pushes (downloads) those new `.pkl` files down to the physical Raspberry Pi over the internet (Over-The-Air update).
5.  **Edge Inference:** The FastAPI app on the Pi reloads the new `.pkl` files into memory and continues making ultra-fast local routing decisions.

*(Note: For our prototype, we are just manually moving the `.pkl` files we generate locally into the same folder as `main.py`.)*

---

## 5. Gemini-CLI Assistant Instructions
As the AI assistant for this workspace, you must follow these rules:
1.  **Act as a Senior Cloud/ML Engineer:** Provide highly optimized, asynchronous, and secure Python code.
2.  **AWS Best Practices:** Use `boto3` efficiently. Ensure all IAM permissions and security boundaries (TLS 1.2+, SSE-S3) are respected in the code logic.
3.  **Step-by-Step Execution:** Do not write the entire system at once. We will begin exclusively with Phase 1 (The Benchmarking Script). Await explicit user prompts before moving to the next phase.