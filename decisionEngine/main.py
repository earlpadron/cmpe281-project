"""
Intelligent Edge-Cloud Task Placement Framework - Phase 3: Edge Decision Engine
-------------------------------------------------------------------------------
IoT Core edition: subscribes to AWS IoT Core MQTT for task dispatch.
No HTTP server, no Tailscale. Pure IoT Core + S3.

Flow:
  Frontend -> S3 input/{task_id}_{filename}   (direct upload via Cognito)
           -> IoT MQTT cmpe281/tasks/request   {task_id, s3_key, filename}
  Pi       -> ML routing decision
           -> EDGE: resize locally -> S3 output/resized_{task_id}_{filename}
           -> CLOUD: Lambda -> S3 output/resized_{task_id}_{filename}
           -> IoT MQTT cmpe281/tasks/response  {task_id, status, download_url, route}
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
import uuid
from io import BytesIO

import boto3
import joblib
import pandas as pd
import psutil
from awscrt import mqtt
from awsiot import mqtt_connection_builder
from PIL import Image

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("cmpe281.decision_engine")

# ---------------------------------------------------------
# Lyapunov Drift-Plus-Penalty Controller
# ---------------------------------------------------------
LYAPUNOV_V          = float(os.environ.get("LYAPUNOV_V", "500.0"))
LYAPUNOV_STATE_FILE = os.environ.get("LYAPUNOV_STATE_FILE", "/tmp/lyapunov_queue.json")


class LyapunovController:
    """
    Thread-safe Lyapunov drift-plus-penalty routing controller.
    Maintains virtual queue Q tracking cumulative cloud cost pressure.
    Decision minimizes: V * latency + Q * cost
    """

    def __init__(self, V: float = LYAPUNOV_V, state_file: str = LYAPUNOV_STATE_FILE):
        self.V          = V
        self.state_file = state_file
        self._lock      = threading.Lock()
        self.Q          = self._load_q()
        log.info("[Lyapunov] Controller initialized — V=%.1f, Q_0=%.6f", self.V, self.Q)

    def _load_q(self) -> float:
        try:
            with open(self.state_file, "r") as f:
                return float(json.load(f).get("Q", 0.0))
        except Exception:
            return 0.0

    def _save_q(self) -> None:
        try:
            with open(self.state_file, "w") as f:
                json.dump({"Q": self.Q}, f)
        except Exception as exc:
            log.warning("[Lyapunov] Could not persist Q: %s", exc)

    def decide(self, pred_edge_lat_ms: float, pred_cloud_lat_ms: float, pred_cloud_cost: float):
        """Returns (route, reason, Q_before, Q_after)."""
        with self._lock:
            Q_before    = self.Q
            score_edge  = self.V * pred_edge_lat_ms
            score_cloud = self.V * pred_cloud_lat_ms + self.Q * pred_cloud_cost

            log.info("[Lyapunov] Q=%.6f | score_edge=%.2f score_cloud=%.2f",
                     self.Q, score_edge, score_cloud)

            if score_cloud < score_edge:
                route  = "CLOUD"
                reason = f"Lyapunov: cloud score ({score_cloud:.2f}) < edge score ({score_edge:.2f})"
                self.Q = max(0.0, self.Q + pred_cloud_cost)
            else:
                route  = "EDGE"
                reason = f"Lyapunov: edge score ({score_edge:.2f}) <= cloud score ({score_cloud:.2f})"
                self.Q = max(0.0, self.Q - USER_BUDGET_USD)

            Q_after = self.Q
            self._save_q()
            return route, reason, Q_before, Q_after


# Singleton — instantiated once at startup after log is ready
lyapunov = LyapunovController()

# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------
BUCKET_NAME   = os.environ.get("S3_BUCKET_NAME",   "your-s3-bucket-name")
BUCKET_REGION = os.environ.get("S3_BUCKET_REGION",  "us-west-1")
LAMBDA_NAME   = os.environ.get("LAMBDA_NAME",       "cmpe281-shared-image-resizer")
AWS_REGION    = os.environ.get("AWS_REGION",         "us-east-1")

IOT_ENDPOINT  = os.environ.get("IOT_ENDPOINT",  "YOUR_IOT_ENDPOINT-ats.iot.us-east-1.amazonaws.com")
IOT_CERT      = os.environ.get("IOT_CERT",      "/greengrass/v2/device.pem.crt")
IOT_KEY       = os.environ.get("IOT_KEY",       "/greengrass/v2/private.pem.key")
IOT_CA        = os.environ.get("IOT_CA",        "/greengrass/v2/AmazonRootCA1.pem")
IOT_CLIENT_ID = os.environ.get("IOT_CLIENT_ID", f"cmpe281-pi-{uuid.uuid4().hex[:8]}")

REQUEST_TOPIC  = "cmpe281/tasks/request"
RESPONSE_TOPIC = "cmpe281/tasks/response"

MAX_CONCURRENCY = int(os.environ.get("MAX_CONCURRENCY_THRESHOLD", "2"))
USER_BUDGET_USD = float(os.environ.get("USER_BUDGET_USD", "0.000050"))

# ---------------------------------------------------------
# Greengrass Token Exchange Credential Provider
# ---------------------------------------------------------
def get_ggc_credentials() -> dict:
    """
    Fetch temporary AWS credentials from the Greengrass Token Exchange Service.
    Used when running as ggc_user inside Greengrass (no ~/.aws/credentials).
    Falls back to default boto3 credential chain if unavailable.
    """
    import urllib.request
    import ssl

    endpoint  = os.environ.get("IOT_CRED_ENDPOINT",
                    "YOUR_CRED_ENDPOINT.credentials.iot.us-east-1.amazonaws.com")
    role      = os.environ.get("IOT_ROLE_ALIAS",
                    "GreengrassV2TokenExchangeCoreDeviceRoleAlias")
    cert      = os.environ.get("IOT_CERT",  "/greengrass/v2/device.pem.crt")
    key       = os.environ.get("IOT_KEY",   "/greengrass/v2/private.pem.key")
    ca        = os.environ.get("IOT_CA",    "/greengrass/v2/AmazonRootCA1.pem")

    url = f"https://{endpoint}/role-aliases/{role}/credentials"
    try:
        ctx = ssl.create_default_context(cafile=ca)
        ctx.load_cert_chain(certfile=cert, keyfile=key)
        with urllib.request.urlopen(url, context=ctx, timeout=5) as resp:
            creds = json.loads(resp.read())["credentials"]
            log.info("Fetched Greengrass Token Exchange credentials (expire: %s)",
                     creds.get("expiration"))
            return creds
    except Exception as exc:
        log.warning("Could not fetch GGC credentials, falling back to default chain: %s", exc)
        return {}


# ---------------------------------------------------------
# AWS clients — refreshed per task via Token Exchange
# ---------------------------------------------------------
def make_boto3_client(service: str, region: str):
    """Create a boto3 client using GGC token exchange creds if available."""
    creds = get_ggc_credentials()
    if creds:
        return boto3.client(
            service,
            region_name=region,
            aws_access_key_id=creds["accessKeyId"],
            aws_secret_access_key=creds["secretAccessKey"],
            aws_session_token=creds["sessionToken"],
        )
    return boto3.client(service, region_name=region)


def get_aws_clients():
    """Return fresh boto3 clients with current credentials."""
    return (
        make_boto3_client("s3",     BUCKET_REGION),
        make_boto3_client("lambda", AWS_REGION),
    )


# Initial clients (used at startup for warming ping etc.)
s3_client, lambda_client = get_aws_clients()

# ---------------------------------------------------------
# Concurrency tracking
# ---------------------------------------------------------
active_tasks = 0
task_lock    = threading.Lock()

# Global MQTT connection reference
mqtt_conn_global = None

# ---------------------------------------------------------
# ML Models
# ---------------------------------------------------------
MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")

try:
    log.info("Loading ML models...")
    edge_lat_model   = joblib.load(os.path.join(MODEL_DIR, "edge_latency_model.pkl"))
    cloud_lat_model  = joblib.load(os.path.join(MODEL_DIR, "cloud_latency_model.pkl"))
    cloud_cost_model = joblib.load(os.path.join(MODEL_DIR, "cloud_cost_model.pkl"))
    log.info("ML models loaded OK.")
except Exception as exc:
    log.warning("Could not load ML models: %s — using fallback routing.", exc)
    edge_lat_model = cloud_lat_model = cloud_cost_model = None

# ---------------------------------------------------------
# Core helpers
# ---------------------------------------------------------
def get_hardware_metrics() -> dict:
    return {
        "edge_cpu_utilization":    psutil.cpu_percent(interval=None),
        "edge_memory_utilization": psutil.virtual_memory().percent,
    }


def ml_inference(file_size_bytes: int, hw_metrics: dict):
    if not edge_lat_model:
        return 1500.0, 400.0, 0.000004

    import socket
    start = time.perf_counter()
    try:
        sock = socket.create_connection(("s3.amazonaws.com", 443), timeout=3)
        sock.close()
    except Exception:
        pass
    rtt_ms = (time.perf_counter() - start) * 1000

    X_edge = pd.DataFrame([{
        "image_size_bytes":        file_size_bytes,
        "edge_cpu_utilization":    hw_metrics["edge_cpu_utilization"],
        "edge_memory_utilization": hw_metrics["edge_memory_utilization"],
    }])
    X_cloud = pd.DataFrame([{
        "image_size_bytes":                file_size_bytes,
        "network_rtt_ms":                  rtt_ms,
        "estimated_uplink_bandwidth_kbps": 10000.0,
    }])

    return (
        float(edge_lat_model.predict(X_edge)[0]),
        float(cloud_lat_model.predict(X_cloud)[0]),
        float(cloud_cost_model.predict(X_cloud)[0]),
    )


def resize_bytes_local(image_bytes: bytes, img_format: str) -> bytes:
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    img = img.resize((800, 800), Image.LANCZOS)
    buf = BytesIO()
    fmt = (img_format or "JPEG").upper()
    if fmt == "JPG":
        fmt = "JPEG"
    img.save(buf, format=fmt)
    return buf.getvalue()


def proactive_warming_ping():
    try:
        log.info("[Warming Ping] Pinging Lambda...")
        lambda_client.invoke(
            FunctionName=LAMBDA_NAME,
            InvocationType="Event",
            Payload=json.dumps({"warm_ping": True}),
        )
    except Exception as exc:
        log.warning("[Warming Ping] %s", exc)


def publish_response(payload: dict):
    if mqtt_conn_global is None:
        log.error("MQTT connection not available")
        return
    mqtt_conn_global.publish(
        topic=RESPONSE_TOPIC,
        payload=json.dumps(payload),
        qos=mqtt.QoS.AT_LEAST_ONCE,
    )


# ---------------------------------------------------------
# Task processor (runs in background thread per task)
# ---------------------------------------------------------
def process_task(task: dict) -> None:
    global active_tasks

    task_id  = task.get("task_id", str(uuid.uuid4()))
    s3_key   = task.get("s3_key")
    filename = task.get("filename", "image.jpg")

    log.info("[%s] Task received — s3_key=%s", task_id, s3_key)

    # Refresh AWS clients with current credentials (token exchange creds expire hourly)
    s3_client, lambda_client = get_aws_clients()

    with task_lock:
        active_tasks += 1
        current_concurrency = active_tasks

    try:
        # 1. Download image from S3
        log.info("[%s] Downloading from S3...", task_id)
        obj       = s3_client.get_object(Bucket=BUCKET_NAME, Key=s3_key)
        img_bytes = obj["Body"].read()
        file_size = len(img_bytes)
        log.info("[%s] Downloaded %d bytes.", task_id, file_size)

        # 2. ML inference + Lyapunov routing decision
        if current_concurrency > MAX_CONCURRENCY:
            route    = "CLOUD"
            reason   = f"Concurrency limit exceeded ({current_concurrency}/{MAX_CONCURRENCY})"
            q_before = lyapunov.Q
            q_after  = lyapunov.Q
        else:
            hw = get_hardware_metrics()
            pred_e, pred_c, pred_cost = ml_inference(file_size, hw)
            log.info("[%s] ML prediction — edge=%.0fms cloud=%.0fms cost=$%.6f",
                     task_id, pred_e, pred_c, pred_cost)
            route, reason, q_before, q_after = lyapunov.decide(pred_e, pred_c, pred_cost)

        log.info("[%s] Route: %s | %s | Q: %.6f -> %.6f",
                 task_id, route, reason, q_before, q_after)

        ext        = filename.rsplit(".", 1)[-1] if "." in filename else "jpg"
        result_key = f"output/resized_{task_id}_{filename}"

        # 3. Execute
        if route == "EDGE":
            threading.Thread(target=proactive_warming_ping, daemon=True).start()
            resized = resize_bytes_local(img_bytes, ext)
            s3_client.put_object(
                Bucket=BUCKET_NAME,
                Key=result_key,
                Body=resized,
                ContentType=f"image/{ext}",
            )
            log.info("[%s] Edge resize complete -> %s", task_id, result_key)

        else:  # CLOUD
            response = lambda_client.invoke(
                FunctionName=LAMBDA_NAME,
                InvocationType="RequestResponse",
                Payload=json.dumps({
                    "bucket":     BUCKET_NAME,
                    "key":        s3_key,
                    "output_key": result_key,
                }),
            )
            resp_payload = json.loads(response["Payload"].read().decode())
            log.info("[%s] Lambda response: %s", task_id, resp_payload)

        # 4. Generate presigned download URL (5 min)
        download_url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET_NAME, "Key": result_key},
            ExpiresIn=300,
        )

        publish_response({
            "task_id":      task_id,
            "status":       "success",
            "route":        route,
            "reason":       reason,
            "download_url": download_url,
            "lyapunov_q":   lyapunov.Q,
        })
        log.info("[%s] Response published to %s", task_id, RESPONSE_TOPIC)

    except Exception as exc:
        log.exception("[%s] Task failed: %s", task_id, exc)
        publish_response({
            "task_id": task_id,
            "status":  "error",
            "error":   str(exc),
        })
    finally:
        with task_lock:
            active_tasks -= 1


# ---------------------------------------------------------
# MQTT message handler
# ---------------------------------------------------------
def on_message_received(topic, payload, dup, qos, retain, **kwargs):
    try:
        task = json.loads(payload.decode("utf-8"))
        log.info("MQTT message on %s: task_id=%s", topic, task.get("task_id"))
        threading.Thread(target=process_task, args=(task,), daemon=True).start()
    except Exception as exc:
        log.error("Failed to parse MQTT message: %s", exc)


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------
def main():
    global mqtt_conn_global

    log.info("Connecting to IoT Core at %s as %s...", IOT_ENDPOINT, IOT_CLIENT_ID)

    mqtt_conn = mqtt_connection_builder.mtls_from_path(
        endpoint=IOT_ENDPOINT,
        cert_filepath=IOT_CERT,
        pri_key_filepath=IOT_KEY,
        ca_filepath=IOT_CA,
        client_id=IOT_CLIENT_ID,
        clean_session=False,
        keep_alive_secs=30,
    )
    mqtt_conn_global = mqtt_conn

    connect_future = mqtt_conn.connect()
    connect_future.result()
    log.info("Connected to IoT Core.")

    subscribe_future, _ = mqtt_conn.subscribe(
        topic=REQUEST_TOPIC,
        qos=mqtt.QoS.AT_LEAST_ONCE,
        callback=on_message_received,
    )
    subscribe_future.result()
    log.info("Subscribed to %s — waiting for tasks...", REQUEST_TOPIC)

    stop_event = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
    signal.signal(signal.SIGINT,  lambda *_: stop_event.set())
    stop_event.wait()

    log.info("Shutting down...")
    mqtt_conn.disconnect().result()
    log.info("Disconnected.")


if __name__ == "__main__":
    main()
