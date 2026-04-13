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
from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse, Response
import boto3      # The official AWS SDK for Python. Used to talk to S3 and Lambda.
import psutil     # Used to sample the Raspberry Pi's CPU and Memory utilization.
from PIL import Image  # Python Imaging Library (Pillow). Used for local image resizing.

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

# ---------------------------------------------------------
# System Configuration Variables
# ---------------------------------------------------------
# These variables define the hardcoded rules and AWS resource names for our framework.
BUCKET_NAME = "cmpe281-benchmark-data-a81aa9e4" # The globally unique S3 bucket we created
LAMBDA_NAME = "cmpe281-image-resizer"          # The name of our deployed AWS Lambda function

# Concurrency is our main bottleneck on the Edge. If we try to resize 3 images at once 
# on a Raspberry Pi, it might overheat or crash. This is our safety limit.
MAX_CONCURRENCY_THRESHOLD = 2                  

# The maximum amount of money (in USD) a user is willing to spend to process an image in the cloud.
USER_BUDGET_USD = 0.000050                     

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

def ml_inference_stub(file_size_bytes, hw_metrics):
    """
    STUB: Phase 2 Machine Learning Pipeline Integration.
    
    Currently, this contains fake math. In Phase 2, you will replace this logic by 
    loading your trained Ridge Regression and GBRT models (e.g., using scikit-learn).
    The models will take `file_size_bytes` and `hw_metrics` as input, and predict
    the exact latency and cost.
    """
    # Fake mathematical calculation to simulate edge latency scaling with CPU load.
    base_edge_latency = (file_size_bytes / 10000) * (hw_metrics["edge_cpu_utilization"] / 10 + 1)
    
    predicted_edge_latency_ms = min(base_edge_latency, 1500.0) 
    predicted_cloud_latency_ms = 400.0 # Assuming a warm AWS Lambda container
    predicted_cloud_cost_usd = 0.000004
    
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
def process_image_edge(image_bytes: bytes, img_format: str) -> bytes:
    """
    Path A: Executes the heavy image resizing locally on the Raspberry Pi's CPU.
    """
    print("[Execution] Processing on Edge Node...")
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
    
    # 4. Construct the public URL where the final, resized image now lives in S3.
    processed_key = f"resized/{filename}"
    s3_url = f"https://{BUCKET_NAME}.s3.amazonaws.com/{processed_key}"
    
    return {
        "status": "success",
        "s3_url": s3_url,
        "lambda_response": response_payload
    }

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
            pred_edge_lat, pred_cloud_lat, pred_cloud_cost = ml_inference_stub(file_size, hw_metrics)
            
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
                
                # asyncio.to_thread() is CRITICAL here. Image resizing blocks the CPU.
                # By pushing it to a separate thread, the main FastAPI server can still 
                # accept new HTTP requests from other users while the resize happens.
                processed_bytes = await asyncio.to_thread(process_image_edge, image_bytes, img_format)
                
                # Return the physical resized image file directly to the user's browser.
                return Response(
                    content=processed_bytes, 
                    media_type=file.content_type,
                    headers={"X-Routing-Decision": "EDGE"}
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
