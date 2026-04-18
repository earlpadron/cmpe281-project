# Project Progress & Implementation Journal

## Phase 3: Edge Decision Engine
*Status: Completed*

### Completed Milestones

#### Phase 1: Data Collection & Benchmarking (Completed 2026-03-12)
*   Implemented `scripts/benchmark.py` and `cloud/lambda_function.py`.
*   Successfully ran Edge (Raspberry Pi/Local CPU) and Cloud (AWS Lambda) benchmarking tasks on a sample of 1,000 Unsplash images.
*   Programmatically calculated precise AWS billing metrics (Lambda `Billed Duration`, `Init Duration` for cold starts) and S3 upload bandwidth.
*   Exported high-fidelity empirical datasets for Machine Learning training.

#### Phase 2: Machine Learning Pipeline (Completed 2026-04-15)
*   Implemented `scripts/train_models.py` utilizing the `scikit-learn` framework.
*   **Edge Latency Model:** Trained a Ridge Regression model to predict local CPU-bound scaling constraints.
*   **Cloud Latency & Cost Models:** Trained Gradient Boosted Regression Trees (GBRT) to predict non-linear network jitter, AWS cold start penalties, and serverless costs.
*   Serialized models to `.pkl` format into `backend/models/`.

#### Phase 3: Edge Decision Engine & Routing Policy (Completed 2026-04-15)
*   Built a highly concurrent, asynchronous REST API using FastAPI (`backend/main.py`).
*   Implemented real-time feature extraction (`psutil` CPU/Mem, `boto3` RTT network pings).
*   Integrated the serialized `.pkl` models to calculate sub-millisecond execution predictions.
*   **Proactive Warming Innovation:** Implemented a non-blocking `FastAPI.BackgroundTasks` ping to AWS Lambda (`InvocationType='Event'`) when Edge execution is selected. This forces AWS to provision the container, eliminating Cold Starts for subsequent overflow traffic.
*   **Secure Public Downloads:** Integrated S3 Presigned URLs with a 5-minute expiration into the Cloud response path. This allows unauthenticated users on the frontend to securely download thumbnails from a strictly blocked, private S3 Data Lake.
*   Integrated dual-path response generation: S3 URL JSON pointers for cloud execution vs. raw byte streaming for edge execution.

### Technical Rationale
*   **Serverless Optimization:** Maintaining a purely serverless architecture prevents idle compute costs. The architecture strictly avoids hosting the backend on EC2, deploying it directly to the physical edge via AWS IoT Greengrass instead.
*   **S3 Pointer Pattern:** Solves the AWS IoT Core 128KB MQTT payload limit by having public users upload raw images directly to S3, using MQTT solely as a lightweight JSON signal.
*   **MLOps Boundary:** Clarified that the heavy ML training phase must happen offline or in the cloud (AWS SageMaker), while the sub-millisecond serialized inference phase strictly runs on the Raspberry Pi memory to eliminate network round trips.