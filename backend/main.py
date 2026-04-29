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
import os         # Used to read environment variables (Greengrass-driven config)
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

# --- Local Imports ---
# Shared resize core. When the API runs as a Greengrass component, the actual
# Pillow work is delegated to the cmpe281.edge_resizer component over IPC and this
# in-process implementation is only used as a fallback (e.g. local dev mode).
from lib.resize import resize_bytes

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
# Resource names default to the shared-account values but can be overridden by
# environment variables -- the Greengrass recipe injects these so the same code
# runs in dev (local uvicorn) and prod (Greengrass component) unchanged.
BUCKET_NAME = os.environ.get("S3_BUCKET_NAME", "cmpe281-shared-benchmark-data-ep-v2")
LAMBDA_NAME = os.environ.get("LAMBDA_NAME", "cmpe281-shared-image-resizer")

# When true, the EDGE execution path delegates to the cmpe281.edge_resizer Greengrass
# component over IPC. When false (default for local dev), we resize in-process.
USE_GREENGRASS_IPC = os.environ.get("USE_GREENGRASS_IPC", "false").lower() in ("1", "true", "yes")

# Concurrency is our main bottleneck on the Edge. If we try to resize 3 images at once
# on a Raspberry Pi, it might overheat or crash. This is our safety limit.
MAX_CONCURRENCY_THRESHOLD = int(os.environ.get("MAX_CONCURRENCY_THRESHOLD", "2"))

# The maximum amount of money (in USD) a user is willing to spend to process an image in the cloud.
USER_BUDGET_USD = float(os.environ.get("USER_BUDGET_USD", "0.000050"))

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
lambda_client = boto3.client('lambda')

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


def is_valid_jpeg_upload(upload: UploadFile, image_bytes: bytes) -> bool:
    """Strict JPEG gate: extension, MIME type, and file signature must match."""
    allowed_mime_types = {"image/jpeg", "image/jpg", "image/pjpeg"}
    filename = (upload.filename or "").lower()
    content_type = (upload.content_type or "").lower()
    has_jpeg_extension = filename.endswith(".jpg") or filename.endswith(".jpeg")
    has_jpeg_mime = content_type in allowed_mime_types
    has_jpeg_signature = len(image_bytes) >= 3 and image_bytes[:3] == b"\xff\xd8\xff"
    return has_jpeg_extension and has_jpeg_mime and has_jpeg_signature

# ---------------------------------------------------------
# Machine Learning Models (Loaded into memory on startup)
# ---------------------------------------------------------
try:
    print("Loading ML Models (.pkl) into memory...")
    edge_lat_model = joblib.load('models/edge_latency_model.pkl')
    cloud_lat_model = joblib.load('models/cloud_latency_model.pkl')
    cloud_cost_model = joblib.load('models/cloud_cost_model.pkl')
except Exception as e:
    print(f"WARNING: Could not load ML models. Ensure Phase 2 is complete. Error: {e}")
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
        print("[Warming Ping] Firing asynchronous ping to AWS Lambda...")
        lambda_client.invoke(
            FunctionName=LAMBDA_NAME,
            # 'Event' is critical here. It tells AWS "Fire and forget." Our Edge server
            # doesn't wait for AWS to reply, keeping our system incredibly fast.
            InvocationType='Event', 
            # We send a specific flag so `lambda_function.py` knows this is just a ping.
            Payload=json.dumps({"warm_ping": True}) 
        )
    except Exception as e:
        print(f"[Warming Ping Error] {e}")


# ---------------------------------------------------------
# Execution Tasks
# ---------------------------------------------------------
def process_image_edge_inproc(image_bytes: bytes, img_format: str) -> bytes:
    """
    Path A (in-process): runs the shared resize core directly on the API
    worker thread. Used for local development when Greengrass IPC is disabled.
    """
    print("[Execution] Processing on Edge Node (in-process)...")
    return resize_bytes(image_bytes, img_format=img_format)


async def process_image_edge_ipc(image_bytes: bytes, img_format: str) -> bytes:
    """
    Path A (Greengrass): delegates the resize to the cmpe281.edge_resizer
    component over local Greengrass IPC. Bytes travel via /tmp/cmpe281/ files
    rather than the IPC payload so multi-MB images go through cleanly.
    """
    print("[Execution] Processing on Edge Node (Greengrass IPC -> cmpe281.edge_resizer)...")
    from ipc_client import get_client
    return await get_client().resize(image_bytes, img_format=img_format)


async def process_image_edge(image_bytes: bytes, img_format: str) -> bytes:
    """Single entry point for the EDGE path; dispatches based on USE_GREENGRASS_IPC."""
    if USE_GREENGRASS_IPC:
        return await process_image_edge_ipc(image_bytes, img_format)
    return await asyncio.to_thread(process_image_edge_inproc, image_bytes, img_format)

def process_image_cloud(image_bytes: bytes, filename: str) -> dict:
    """
    Path B: Offloads the payload to AWS for Serverless processing.
    """
    print("[Execution] Processing on Cloud Node...")
    
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

# ---------------------------------------------------------
# API Endpoints (The URLs the frontend talks to)
# ---------------------------------------------------------

@app.on_event("startup")
async def _warm_ipc_client():
    """If running under Greengrass, eagerly establish the IPC subscription so
    the first user request doesn't pay the connect+subscribe penalty."""
    if not USE_GREENGRASS_IPC:
        return
    try:
        from ipc_client import get_client
        await get_client().start()
        print("[Startup] Greengrass IPC client connected to cmpe281.edge_resizer.")
    except Exception as exc:  # noqa: BLE001
        # Don't crash the API if IPC is unavailable; we'll surface this on /health.
        print(f"[Startup] WARNING: Greengrass IPC unavailable: {exc}")


@app.get("/health")
def health():
    """A simple endpoint used by load balancers or monitors to check if the Pi is alive."""
    return {
        "status": "ok",
        "active_tasks": active_tasks,
        "edge_backend": "greengrass" if USE_GREENGRASS_IPC else "in-process",
        "models_loaded": edge_lat_model is not None,
    }

@app.post("/resize")
async def resize_image(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """
    MAIN ENDPOINT: This is where the frontend sends the user's image.
    It orchestrates the entire intelligence and routing workflow.
    """
    global active_tasks
    
    # Safely lock the thread to increment our concurrency counter.
    # We must know exactly how many images are currently being processed.
    with task_lock:
        active_tasks += 1
        current_concurrency = active_tasks

    # Use a try/finally block to guarantee we decrement the counter when we finish, 
    # even if an error crashes the code in the middle.
    try:
        # Read the uploaded image from the user's HTTP request into the Pi's RAM
        image_bytes = await file.read()
        file_size = len(image_bytes)
        
        if file_size == 0:
            raise HTTPException(status_code=400, detail="Empty file uploaded.")

        if not is_valid_jpeg_upload(file, image_bytes):
            raise HTTPException(
                status_code=400,
                detail="Only JPEG files are allowed (.jpg/.jpeg with image/jpeg content type).",
            )
            
        # ========================================================
        # STEP 1: ROUTING POLICY EVALUATION
        # ========================================================
        
        # Rule 1: The Hard Threshold. 
        # If the Pi is already processing too many images, it will overheat. 
        # We bypass ML and immediately route to the Cloud for safety.
        if current_concurrency > MAX_CONCURRENCY_THRESHOLD:
            routing_decision = "CLOUD"
            reason = f"Concurrency Limit Exceeded (Active: {current_concurrency}/{MAX_CONCURRENCY_THRESHOLD})"
        
        else:
            # Rule 2: Machine Learning Inference
            # The Pi is safe, so we ask our ML models to predict the future.
            hw_metrics = get_hardware_metrics()
            pred_edge_lat, pred_cloud_lat, pred_cloud_cost = ml_inference(file_size, hw_metrics)
            
            # Rule 3: Optimization Placement Strategy
            # We only send to the cloud if it's FASTER *and* CHEAPER than the user's budget.
            if (pred_cloud_lat < pred_edge_lat) and (pred_cloud_cost <= USER_BUDGET_USD):
                routing_decision = "CLOUD"
                reason = "Cloud optimized (Lower predicted latency & within budget)"
            else:
                routing_decision = "EDGE"
                reason = "Edge optimized (Lower predicted latency or cloud budget exceeded)"

        # Log the decision to the terminal for debugging
        print(f"[{file.filename}] Route: {routing_decision} | Reason: {reason}")
        
        # ========================================================
        # STEP 2: EXECUTION PHASE
        # ========================================================
        # Generate a random unique ID so multiple users uploading "image.jpg" don't overwrite each other.
        request_id = str(uuid.uuid4())
        unique_filename = f"{request_id}_{file.filename}"
        
        if routing_decision == "EDGE":
            
            # INNOVATION: As we start processing locally, we tell FastAPI to run our 
            # "Warming Ping" function in the background. The user doesn't wait for this.
            background_tasks.add_task(proactive_warming_ping)
            
            try:
                img_format = file.content_type.split("/")[-1] if file.content_type else "JPEG"

                # process_image_edge is async: it either awaits the Greengrass IPC
                # round-trip OR off-loads the in-process Pillow call to a thread,
                # depending on USE_GREENGRASS_IPC.
                processed_bytes = await process_image_edge(image_bytes, img_format)

                return Response(
                    content=processed_bytes,
                    media_type=file.content_type,
                    headers={
                        "X-Routing-Decision": "EDGE",
                        "X-Edge-Backend": "greengrass" if USE_GREENGRASS_IPC else "in-process",
                    },
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Edge Processing Failed: {str(e)}")
                
        elif routing_decision == "CLOUD":
            
            try:
                # AWS boto3 network calls also block the CPU, so we thread them too.
                cloud_result = await asyncio.to_thread(process_image_cloud, image_bytes, unique_filename)
                
                # Instead of returning the raw image bytes (which would use up Pi bandwidth),
                # we just return a lightweight JSON message with the S3 URL. The frontend
                # will use that URL to download the image directly from Amazon.
                return JSONResponse(status_code=200, content={
                    "route": "CLOUD",
                    "reason": reason,
                    "result": cloud_result
                })
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Cloud Processing Failed: {str(e)}")

    finally:
        # STEP 3: CLEANUP
        # Decrease the concurrency counter so the next user knows the Pi is free.
        with task_lock:
            active_tasks -= 1
