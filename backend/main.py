"""
Intelligent Edge-Cloud Task Placement Framework - Phase 3: Edge Decision Engine
-------------------------------------------------------------------------------
This file (`main.py`) acts as the "Brain" of our system. It runs on the Edge device
(Raspberry Pi) and provides a REST API for the frontend web application. 

Its primary job is to receive an image upload, evaluate current hardware/network 
conditions, consult a Machine Learning model (Phase 2), and dynamically decide 
whether to process the image locally (Edge) or send it to AWS (Cloud).
"""

# --- Standard Library Imports ---
import uuid       # Used to generate unique IDs for each image request
import json       # Used to parse and format JSON data for AWS payloads
import asyncio    # Used for running asynchronous tasks without blocking the main server
import threading  # Used for thread-safe counters (tracking how many users are active)
from io import BytesIO  # Used to handle raw image bytes in memory (no writing to disk)

# --- Third-Party Imports ---
import joblib     # For loading our .pkl Machine Learning models
import pandas as pd # For formatting inference data
import time       # For quick network RTT measurements
from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
import boto3      # The official AWS SDK for Python. Used to talk to S3 and Lambda.
import psutil     # Used to sample the Raspberry Pi's CPU and Memory utilization.
from PIL import Image  # Python Imaging Library (Pillow). Used for local image resizing.

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("edge_cloud_router")


def log_resize_request(
    *,
    request_id: str,
    filename: str | None,
    file_size_bytes: int,
    current_concurrency: int,
    hw_metrics: dict,
    predicted_edge_latency_ms: float | None,
    predicted_cloud_latency_ms: float | None,
    predicted_cloud_cost_usd: float | None,
    routing_decision: str | None,
    routing_mode: str,
    reason: str | None,
    request_start_perf: float,
    execution_time_ms: float | None,
    status: str,
    error_message: str | None,
    edge_score: float | None,
    cloud_score: float | None,
    queue_pressure: float | None,
    cost_ratio: float | None,
) -> None:
    """One structured log line per /resize request for offline analysis."""
    actual_total_time_ms = (time.perf_counter() - request_start_perf) * 1000.0

    log_data = {
        "request_id": request_id,
        "filename": filename or "",
        "file_size_bytes": file_size_bytes,
        "current_concurrency": current_concurrency,
        "edge_cpu_utilization": hw_metrics.get("edge_cpu_utilization"),
        "edge_memory_utilization": hw_metrics.get("edge_memory_utilization"),
        "predicted_edge_latency_ms": predicted_edge_latency_ms,
        "predicted_cloud_latency_ms": predicted_cloud_latency_ms,
        "predicted_cloud_cost_usd": predicted_cloud_cost_usd,
        "routing_decision": routing_decision,
        "routing_mode": routing_mode,
        "reason": reason,
        "execution_time_ms": round(execution_time_ms, 3) if execution_time_ms is not None else None,
        "actual_total_time_ms": round(actual_total_time_ms, 3),
        "status": status,
        "error_message": error_message,
        "edge_score": round(edge_score, 3) if edge_score is not None else None,
        "cloud_score": round(cloud_score, 3) if cloud_score is not None else None,
        "queue_pressure": round(queue_pressure, 3) if queue_pressure is not None else None,
        "cost_ratio": round(cost_ratio, 6) if cost_ratio is not None else None,
    }

    logger.info(json.dumps(log_data, default=str))
# ---------------------------------------------------------
# FastAPI App Initialization
# ---------------------------------------------------------
# This creates the main web server application. FastAPI uses modern Python async/await,
# which allows our Raspberry Pi to handle many concurrent user requests efficiently.
app = FastAPI(
    title="Intelligent Edge-Cloud Routing API",
    version="1.0.0",
    description="Phase 3: Real-time ML routing between Raspberry Pi and AWS Lambda."
)

# Enable CORS so the frontend (which may be hosted on S3 or opened locally)
# can successfully send requests to this API without security blocks.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all HTTP methods (GET, POST, etc.)
    allow_headers=["*"],  # Allows all headers
    expose_headers=["*"]  # Crucial: allows JS to read our custom 'X-Routing-Decision' header
)

# ---------------------------------------------------------
# System Configuration Variables
# ---------------------------------------------------------
# These variables define the hardcoded rules and AWS resource names for our framework.
BUCKET_NAME = "cmpe281-shared-benchmark-data-ep-v2" # The globally unique S3 bucket we created
LAMBDA_NAME = "cmpe281-shared-image-resizer"          # The name of our deployed AWS Lambda function

# Concurrency is our main bottleneck on the Edge. If we try to resize 3 images at once 
# on a Raspberry Pi, it might overheat or crash. This is our safety limit.
MAX_CONCURRENCY_THRESHOLD = 2                  

# The maximum amount of money (in USD) a user is willing to spend to process an image in the cloud.
USER_BUDGET_USD = 0.00050                     

# Temporary testing flag: force all requests to cloud path.
# Set back to False when you want normal auto-routing behavior.
#FORCE_CLOUD_ROUTING = True
FORCE_ROUTE = None   # Options: None, "EDGE", "CLOUD"
# Lyapunov-inspired routing controls
USE_LYAPUNOV_ROUTING = True

# V controls how strongly we penalize cloud cost.
# Higher V = more conservative about using cloud.
LYAPUNOV_V = 10000.0

# Extra penalty multiplier for choosing EDGE when the system is already busy.
EDGE_QUEUE_WEIGHT = 1.0
# ---------------------------------------------------------
# Global State & AWS Clients
# ---------------------------------------------------------
# We need to track exactly how many users are currently having images processed by the Pi.
active_tasks = 0
# A Lock ensures that if two users upload an image at the exact same millisecond, 
# our `active_tasks` counter updates correctly without race conditions.
task_lock = threading.Lock()

# Initialize our AWS clients globally so we don't have to reconnect for every request.
# Boto3 uses the credentials we configured via `aws configure` in the terminal.
s3_client = boto3.client('s3')
lambda_client = boto3.client("lambda", region_name="us-east-1")

# ---------------------------------------------------------
# Core System Functions
# ---------------------------------------------------------
def get_hardware_metrics():
    """
    Captures the immediate physical state of the Edge device (Raspberry Pi).
    These metrics are crucial features for our ML prediction model.
    """
    return {
        # CPU utilization over a tiny fraction of time. Interval=None makes it instant.
        "edge_cpu_utilization": psutil.cpu_percent(interval=None),
        # Current physical memory (RAM) usage percentage.
        "edge_memory_utilization": psutil.virtual_memory().percent
    }

# ---------------------------------------------------------
# Machine Learning Models (Loaded into memory on startup)
# ---------------------------------------------------------
try:
    logger.info("Loading ML models (.pkl) into memory...")
    edge_lat_model = joblib.load('models/edge_latency_model.pkl')
    cloud_lat_model = joblib.load('models/cloud_latency_model.pkl')
    cloud_cost_model = joblib.load('models/cloud_cost_model.pkl')
except Exception as e:
    logger.warning("Could not load ML models; inference will use fallback values. Error: %s", e)
    edge_lat_model = cloud_lat_model = cloud_cost_model = None

def ml_inference(file_size_bytes, hw_metrics):
    """
    Phase 2 Machine Learning Pipeline Integration.
    
    Uses trained Ridge Regression and GBRT models to mathematically predict 
    latency and cost based on empirical benchmark data.
    """
    if not edge_lat_model:
        # Fallback if models are missing
        return 1500.0, 400.0, 0.000004
        
    # 1. Quick network ping to gauge current RTT to AWS
    start_rtt = time.perf_counter()
    try:
        s3_client.head_bucket(Bucket=BUCKET_NAME)
    except Exception:
        pass
    rtt_ms = (time.perf_counter() - start_rtt) * 1000
    
    # Estimate uplink bandwidth (we use a static average for inference speed, 
    # but in a production system this would be a rolling average of past uploads)
    estimated_uplink_bandwidth_kbps = 10000.0
    
    # 2. Format features exactly as the models were trained (using Pandas)
    X_edge = pd.DataFrame([{
        'image_size_bytes': file_size_bytes,
        'edge_cpu_utilization': hw_metrics['edge_cpu_utilization'],
        'edge_memory_utilization': hw_metrics['edge_memory_utilization']
    }])

    X_cloud = pd.DataFrame([{
        'image_size_bytes': file_size_bytes,
        'network_rtt_ms': rtt_ms,
        'estimated_uplink_bandwidth_kbps': estimated_uplink_bandwidth_kbps
    }])
    
    # 3. Inference (Less than 1 millisecond execution time!)
    predicted_edge_latency_ms = float(edge_lat_model.predict(X_edge)[0])
    predicted_cloud_latency_ms = float(cloud_lat_model.predict(X_cloud)[0])
    predicted_cloud_cost_usd = float(cloud_cost_model.predict(X_cloud)[0])
    
    return predicted_edge_latency_ms, predicted_cloud_latency_ms, predicted_cloud_cost_usd

def proactive_warming_ping():
    """
    INNOVATION FEATURE: Asynchronous Proactive Warming.
    
    If the system decides to process an image locally on the Edge, we know the Edge 
    is currently getting busier. To prepare for future overflow traffic, we ping 
    AWS Lambda in the background. This forces AWS to provision a Firecracker microVM 
    container so it's "warm" and ready for the next user, eliminating the Cold Start penalty.
    """
    try:
        logger.debug("[Warming Ping] Firing asynchronous ping to AWS Lambda...")
        lambda_client.invoke(
            FunctionName=LAMBDA_NAME,
            # 'Event' is critical here. It tells AWS "Fire and forget." Our Edge server
            # doesn't wait for AWS to reply, keeping our system incredibly fast.
            InvocationType='Event', 
            # We send a specific flag so `lambda_function.py` knows this is just a ping.
            Payload=json.dumps({"warm_ping": True}) 
        )
    except Exception as e:
        logger.warning("[Warming Ping Error] %s", e)


# ---------------------------------------------------------
# Execution Tasks
# ---------------------------------------------------------
def process_image_edge(image_bytes: bytes, img_format: str) -> bytes:
    """
    Path A: Executes the heavy image resizing locally on the Raspberry Pi's CPU.
    """
    logger.debug("[Execution] Processing on Edge Node...")
    # Load the raw bytes into a Pillow Image object in memory
    img = Image.open(BytesIO(image_bytes))
    
    # Perform the computationally expensive resize operation (using high-quality LANCZOS filter)
    resized_img = img.resize((800, 800), Image.Resampling.LANCZOS)
    
    # Save the processed image back into a raw byte buffer to send back to the user
    buffer = BytesIO()
    
    # Edge case: Pillow expects 'JPEG' instead of 'JPG'
    save_format = img_format.upper() if img_format else "JPEG"
    if save_format == "JPG": save_format = "JPEG"
    
    resized_img.save(buffer, format=save_format)
    return buffer.getvalue() # Return the raw bytes

def process_image_cloud(image_bytes: bytes, filename: str) -> dict:
    """
    Path B: Offloads the payload to AWS for Serverless processing.
    """
    logger.debug("[Execution] Processing on Cloud Node...")
    
    # 1. Upload the raw image bytes from the user directly to Amazon S3
    s3_client.put_object(Bucket=BUCKET_NAME, Key=filename, Body=image_bytes)
    
    # 2. Command AWS Lambda to start computing.
    # 'RequestResponse' means we WAIT here until Lambda finishes resizing the image in the cloud.
    response = lambda_client.invoke(
        FunctionName=LAMBDA_NAME,
        InvocationType='RequestResponse',
        Payload=json.dumps({'bucket': BUCKET_NAME, 'key': filename})
    )
    
    # 3. Parse the JSON response sent back by our `lambda_function.py` script.
    response_payload = json.loads(response['Payload'].read().decode("utf-8"))
    
    # 4. Construct a secure Presigned URL so the user's browser can download the private file.
    # This URL automatically expires after 5 minutes (300 seconds), maintaining our strict security.
    processed_key = f"resized/{filename}"
    s3_url = s3_client.generate_presigned_url(
        'get_object',
        Params={'Bucket': BUCKET_NAME, 'Key': processed_key},
        ExpiresIn=300
    )
    
    return {
        "status": "success",
        "s3_url": s3_url,
        "lambda_response": response_payload
    }
def lyapunov_route_decision(
    *,
    current_concurrency: int,
    max_concurrency_threshold: int,
    predicted_edge_latency_ms: float,
    predicted_cloud_latency_ms: float,
    predicted_cloud_cost_usd: float,
    user_budget_usd: float,
) -> tuple[str, str, dict]:
    """
    Lyapunov-inspired drift-plus-penalty routing.

    We approximate queue pressure using current concurrency. EDGE incurs more penalty
    as the system gets busier. CLOUD incurs latency plus a weighted cost penalty.

    Returns:
        routing_decision: "EDGE" or "CLOUD"
        reason: human-readable explanation
        policy_debug: dict of intermediate scores for logging/debugging
    """

    # Safety guard: if edge is already overloaded, force cloud.
    if current_concurrency > max_concurrency_threshold:
        return (
            "CLOUD",
            f"Lyapunov override: concurrency limit exceeded ({current_concurrency}/{max_concurrency_threshold})",
            {
                "edge_score": None,
                "cloud_score": None,
                "queue_pressure": None,
                "cost_ratio": None,
            },
        )

    # Normalize congestion to [0, 1] at the threshold, >1 past it.
    queue_pressure = current_concurrency / max_concurrency_threshold if max_concurrency_threshold > 0 else 1.0

    # Normalize cost against the user budget.
    # If cost_ratio > 1, cloud is over budget.
    cost_ratio = (
        predicted_cloud_cost_usd / user_budget_usd
        if user_budget_usd > 0
        else float("inf")
    )

    # EDGE gets more expensive as the edge becomes busier.
    edge_score = predicted_edge_latency_ms * (1.0 + EDGE_QUEUE_WEIGHT * queue_pressure)

    # CLOUD gets latency plus weighted cost penalty.
    cloud_score = predicted_cloud_latency_ms + LYAPUNOV_V * cost_ratio

    # Optional hard budget rule: if cloud cost exceeds budget, reject cloud.
    if predicted_cloud_cost_usd > user_budget_usd:
        return (
            "EDGE",
            "Lyapunov policy chose EDGE because predicted cloud cost exceeds budget",
            {
                "edge_score": edge_score,
                "cloud_score": cloud_score,
                "queue_pressure": queue_pressure,
                "cost_ratio": cost_ratio,
            },
        )

    if cloud_score < edge_score:
        return (
            "CLOUD",
            "Lyapunov policy chose CLOUD (lower drift-plus-penalty score)",
            {
                "edge_score": edge_score,
                "cloud_score": cloud_score,
                "queue_pressure": queue_pressure,
                "cost_ratio": cost_ratio,
            },
        )

    return (
        "EDGE",
        "Lyapunov policy chose EDGE (lower drift-plus-penalty score)",
        {
            "edge_score": edge_score,
            "cloud_score": cloud_score,
            "queue_pressure": queue_pressure,
            "cost_ratio": cost_ratio,
        },
    )
# ---------------------------------------------------------
# API Endpoints (The URLs the frontend talks to)
# ---------------------------------------------------------

@app.get("/health")
def health():
    """A simple endpoint used by load balancers or monitors to check if the Pi is alive."""
    return {"status": "ok", "active_tasks": active_tasks}

@app.post("/resize")
async def resize_image(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """
    MAIN ENDPOINT: This is where the frontend sends the user's image.
    It orchestrates the entire intelligence and routing workflow.
    """
    global active_tasks

    request_id = str(uuid.uuid4())
    request_start_perf = time.perf_counter()

    filename = file.filename
    file_size_bytes = 0
    current_concurrency = 0
    hw_metrics: dict = {}
    pred_edge_lat: float | None = None
    pred_cloud_lat: float | None = None
    pred_cloud_cost: float | None = None
    routing_decision: str | None = None
    routing_mode = "normal"
    reason: str | None = None
    execution_time_ms: float | None = None
    error_message: str | None = None
    status = "error"

    edge_score = None
    cloud_score = None
    queue_pressure = None
    cost_ratio = None

    with task_lock:
        active_tasks += 1
        current_concurrency = active_tasks

    try:
        image_bytes = await file.read()
        file_size_bytes = len(image_bytes)

        if file_size_bytes == 0:
            status = "empty_file"
            raise HTTPException(status_code=400, detail="Empty file uploaded.")

        # ========================================================
        # STEP 1: ROUTING POLICY EVALUATION
        # ========================================================

        if FORCE_ROUTE == "CLOUD":
            routing_decision = "CLOUD"
            routing_mode = "forced_cloud"
            reason = "Forced cloud routing (temporary test override)"

        elif FORCE_ROUTE == "EDGE":
            routing_decision = "EDGE"
            routing_mode = "forced_edge"
            reason = "Forced edge routing (temporary test override)"

        else:
            # Only gather metrics / run ML during normal routing mode
            hw_metrics = get_hardware_metrics()
            pred_edge_lat, pred_cloud_lat, pred_cloud_cost = ml_inference(file_size_bytes, hw_metrics)

            if USE_LYAPUNOV_ROUTING:
                routing_decision, reason, policy_debug = lyapunov_route_decision(
                    current_concurrency=current_concurrency,
                    max_concurrency_threshold=MAX_CONCURRENCY_THRESHOLD,
                    predicted_edge_latency_ms=pred_edge_lat,
                    predicted_cloud_latency_ms=pred_cloud_lat,
                    predicted_cloud_cost_usd=pred_cloud_cost,
                    user_budget_usd=USER_BUDGET_USD,
                )

                edge_score = policy_debug["edge_score"]
                cloud_score = policy_debug["cloud_score"]
                queue_pressure = policy_debug["queue_pressure"]
                cost_ratio = policy_debug["cost_ratio"]
                routing_mode = "lyapunov"

            else:
                if current_concurrency > MAX_CONCURRENCY_THRESHOLD:
                    routing_decision = "CLOUD"
                    reason = f"Concurrency Limit Exceeded (Active: {current_concurrency}/{MAX_CONCURRENCY_THRESHOLD})"
                elif (pred_cloud_lat < pred_edge_lat) and (pred_cloud_cost <= USER_BUDGET_USD):
                    routing_decision = "CLOUD"
                    reason = "Cloud optimized (Lower predicted latency & within budget)"
                else:
                    routing_decision = "EDGE"
                    reason = "Edge optimized (Lower predicted latency or cloud budget exceeded)"
        # ========================================================
        # STEP 2: EXECUTION PHASE
        # ========================================================
        safe_name = filename or "upload"
        unique_filename = f"{request_id}_{safe_name}"

        if routing_decision == "EDGE":
            background_tasks.add_task(proactive_warming_ping)

            try:
                img_format = file.content_type.split("/")[-1] if file.content_type else "JPEG"

                execution_start = time.perf_counter()
                processed_bytes = await asyncio.to_thread(process_image_edge, image_bytes, img_format)
                execution_time_ms = (time.perf_counter() - execution_start) * 1000.0

                status = "success"
                return Response(
                    content=processed_bytes,
                    media_type=file.content_type,
                    headers={"X-Routing-Decision": "EDGE"},
                )

            except Exception as e:
                execution_time_ms = (time.perf_counter() - execution_start) * 1000.0 if 'execution_start' in locals() else None
                status = "edge_processing_failed"
                error_message = str(e)
                raise HTTPException(status_code=500, detail=f"Edge Processing Failed: {str(e)}") from e

        elif routing_decision == "CLOUD":
            try:
                execution_start = time.perf_counter()
                cloud_result = await asyncio.to_thread(process_image_cloud, image_bytes, unique_filename)
                execution_time_ms = (time.perf_counter() - execution_start) * 1000.0

                status = "success"
                return JSONResponse(
                    status_code=200,
                    content={
                        "route": "CLOUD",
                        "reason": reason,
                        "result": cloud_result,
                    },
                    headers={"X-Routing-Decision": "CLOUD"}
                )

            except Exception as e:
                execution_time_ms = (time.perf_counter() - execution_start) * 1000.0 if 'execution_start' in locals() else None
                status = "cloud_processing_failed"
                error_message = str(e)
                raise HTTPException(status_code=500, detail=f"Cloud Processing Failed: {str(e)}") from e

        status = "unknown_routing_decision"
        error_message = "No routing decision matched."
        raise HTTPException(status_code=500, detail="No routing decision matched.")

    except HTTPException as e:
        if status == "error":
            status = f"http_{e.status_code}"
            error_message = e.detail if isinstance(e.detail, str) else str(e.detail)
        raise

    finally:
        log_resize_request(
            request_id=request_id,
            filename=filename,
            file_size_bytes=file_size_bytes,
            current_concurrency=current_concurrency,
            hw_metrics=hw_metrics,
            predicted_edge_latency_ms=pred_edge_lat,
            predicted_cloud_latency_ms=pred_cloud_lat,
            predicted_cloud_cost_usd=pred_cloud_cost,
            routing_decision=routing_decision,
            routing_mode=routing_mode,
            reason=reason,
            request_start_perf=request_start_perf,
            execution_time_ms=execution_time_ms,
            status=status,
            error_message=error_message,
            edge_score=edge_score,
            cloud_score=cloud_score,
            queue_pressure=queue_pressure,
            cost_ratio=cost_ratio,
        )

        with task_lock:
            active_tasks -= 1
