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

# ---------------------------------------------------------
# Lyapunov Drift-Plus-Penalty Controller
# ---------------------------------------------------------
# Theory:
#   We maintain a virtual queue Q that tracks cumulative cloud cost "debt".
#   Every time we route to CLOUD, Q increases by the predicted cost.
#   Every time we route to EDGE (cost = 0), Q drains toward 0.
#
#   Each routing decision minimizes the combined objective:
#       min over {EDGE, CLOUD}:  V * predicted_latency  +  Q * predicted_cloud_cost
#
#   - V (lyapunov_v): trade-off knob. Higher V = more latency-sensitive.
#     Lower V = more willing to absorb cost to drain the queue.
#   - Q grows when we spend cloud budget; this makes future cloud decisions
#     less likely, giving provable long-run average cost bounds.
#
# Integration: This is a drop-in replacement for the per-request greedy
# comparison in process_task(). Everything else (S3, Lambda, MQTT) is untouched.

LYAPUNOV_V         = float(os.environ.get("LYAPUNOV_V",          "500.0"))
LYAPUNOV_STATE_FILE = os.environ.get("LYAPUNOV_STATE_FILE", "/tmp/lyapunov_queue.json")


class LyapunovController:
    """
    Thread-safe Lyapunov drift-plus-penalty routing controller.

    Virtual queue Q tracks cumulative cloud cost pressure. Routing decisions
    minimize V * latency + Q * cost, giving stable long-run average cost
    while remaining adaptive to instantaneous latency predictions.
    """

    def __init__(self, V: float = LYAPUNOV_V, state_file: str = LYAPUNOV_STATE_FILE):
        self.V          = V
        self.state_file = state_file
        self._lock      = threading.Lock()
        self.Q          = self._load_q()
        log.info("[Lyapunov] Controller initialized — V=%.1f, Q_0=%.6f", self.V, self.Q)

    # ------------------------------------------------------------------
    # State persistence (survive Pi restarts)
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Core decision
    # ------------------------------------------------------------------
    def decide(
        self,
        pred_edge_lat_ms:  float,
        pred_cloud_lat_ms: float,
        pred_cloud_cost:   float,
    ) -> tuple[str, str, float, float]:
        """
        Returns (route, reason, Q_before, Q_after).

        Objective per route:
          EDGE  score = V * pred_edge_lat_ms   + Q * 0              (no cost)
          CLOUD score = V * pred_cloud_lat_ms  + Q * pred_cloud_cost

        Lower score wins.

        Queue update (max(·,0) keeps Q non-negative):
          CLOUD chosen  -> Q += pred_cloud_cost   (cost debt grows)
          EDGE  chosen  -> Q = max(0, Q - USER_BUDGET_USD)  (drain by budget slot)
        """
        with self._lock:
            Q_before = self.Q

            score_edge  = self.V * pred_edge_lat_ms
            score_cloud = self.V * pred_cloud_lat_ms + self.Q * pred_cloud_cost

            log.info(
                "[Lyapunov] Q=%.6f | score_edge=%.2f score_cloud=%.2f",
                self.Q, score_edge, score_cloud,
            )

            if score_cloud < score_edge:
                route  = "CLOUD"
                reason = (
                    f"Lyapunov: cloud score ({score_cloud:.2f}) < "
                    f"edge score ({score_edge:.2f})"
                )
                self.Q = max(0.0, self.Q + pred_cloud_cost)
            else:
                route  = "EDGE"
                reason = (
                    f"Lyapunov: edge score ({score_edge:.2f}) <= "
                    f"cloud score ({score_cloud:.2f})"
                )
                self.Q = max(0.0, self.Q - USER_BUDGET_USD)

            Q_after = self.Q
            self._save_q()
            return route, reason, Q_before, Q_after



logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("cmpe281.decision_engine")

# Singleton controller — loaded once at startup
lyapunov = LyapunovController()

# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------
BUCKET_NAME   = os.environ.get("S3_BUCKET_NAME",   "cmpe281-resize-bucket-549955691205-us-west-1-an")
BUCKET_REGION = os.environ.get("S3_BUCKET_REGION",  "us-west-1")
LAMBDA_NAME   = os.environ.get("LAMBDA_NAME",       "cmpe281-shared-image-resizer")
AWS_REGION    = os.environ.get("AWS_REGION",         "us-east-1")

IOT_ENDPOINT  = os.environ.get("IOT_ENDPOINT",  "a23lh66sqvs76c-ats.iot.us-east-1.amazonaws.com")
IOT_CERT      = os.environ.get("IOT_CERT",      "/greengrass/v2/device.pem.crt")
IOT_KEY       = os.environ.get("IOT_KEY",       "/greengrass/v2/private.pem.key")
IOT_CA        = os.environ.get("IOT_CA",        "/greengrass/v2/AmazonRootCA1.pem")
IOT_CLIENT_ID = os.environ.get("IOT_CLIENT_ID", f"cmpe281-pi-{uuid.uuid4().hex[:8]}")

REQUEST_TOPIC  = "cmpe281/tasks/request"
RESPONSE_TOPIC = "cmpe281/tasks/response"

MAX_CONCURRENCY = int(os.environ.get("MAX_CONCURRENCY_THRESHOLD", "2"))
USER_BUDGET_USD = float(os.environ.get("USER_BUDGET_USD", "0.000050"))

# ---------------------------------------------------------
# AWS clients
# ---------------------------------------------------------
s3_client     = boto3.client("s3", region_name=BUCKET_REGION)
lambda_client = boto3.client("lambda", region_name=AWS_REGION)

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

    start = time.perf_counter()
    try:
        s3_client.head_bucket(Bucket=BUCKET_NAME)
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

        # 2. ML routing decision + Lyapunov drift-plus-penalty optimization
        if current_concurrency > MAX_CONCURRENCY:
            # Hard safety limit: Pi is saturated, bypass Lyapunov and force cloud.
            route     = "CLOUD"
            reason    = f"Concurrency limit exceeded ({current_concurrency}/{MAX_CONCURRENCY})"
            q_before  = lyapunov.Q
            q_after   = lyapunov.Q
        else:
            hw = get_hardware_metrics()
            pred_e, pred_c, pred_cost = ml_inference(file_size, hw)
            log.info("[%s] ML prediction — edge=%.0fms cloud=%.0fms cost=$%.6f",
                     task_id, pred_e, pred_c, pred_cost)

            # Lyapunov controller replaces the greedy comparison.
            # It accounts for cumulative cost pressure (virtual queue Q)
            # in addition to instantaneous latency predictions.
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
