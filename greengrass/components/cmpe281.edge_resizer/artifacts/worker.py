"""
cmpe281.edge_resizer Greengrass v2 component.

Long-running worker that subscribes to a local pub/sub topic, runs the shared
`resize_lib.resize_file(...)` core on a temp file, and publishes the result
back. This is the EDGE execution path that previously ran in-process inside
`backend/main.py::process_image_edge`.

Wire format
-----------
Request topic : cmpe281/edge/resize/request
    {
      "request_id":  "<uuid>",
      "input_path":  "/tmp/cmpe281/<uuid>.in.jpg",
      "output_path": "/tmp/cmpe281/<uuid>.out.jpg",
      "format":      "JPEG"        # optional override
    }

Response topic: cmpe281/edge/resize/response
    {
      "request_id":  "<uuid>",
      "status":      "success" | "error",
      "output_path": "/tmp/cmpe281/<uuid>.out.jpg",
      "elapsed_ms":  12.4,
      "error":       "<message>"   # only on error
    }

We intentionally *do not* put image bytes in IPC payloads — Greengrass IPC
local pub/sub messages are bounded, and even if base64 fits, the marshalling
overhead is wasteful when both processes share the same disk.

Concurrency
-----------
Bounded by `MAX_INFLIGHT` (env: `RESIZE_MAX_INFLIGHT`, default 2) to mirror the
hard CPU concurrency cap that `backend/main.py` enforces today
(MAX_CONCURRENCY_THRESHOLD).
"""

import json
import os
import sys
import time
import logging
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

from awsiot.greengrasscoreipc.clientv2 import GreengrassCoreIPCClientV2
from awsiot.greengrasscoreipc.model import (
    BinaryMessage,
    PublishMessage,
    SubscriptionResponseMessage,
)

from resize_lib import resize_file

# ---------- Configuration ----------------------------------------------------
REQUEST_TOPIC = os.environ.get("RESIZE_REQUEST_TOPIC", "cmpe281/edge/resize/request")
RESPONSE_TOPIC = os.environ.get("RESIZE_RESPONSE_TOPIC", "cmpe281/edge/resize/response")
MAX_INFLIGHT = int(os.environ.get("RESIZE_MAX_INFLIGHT", "2"))

# ---------- Logging ----------------------------------------------------------
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] [resize-worker] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("cmpe281.edge_resizer")


def _publish(ipc: GreengrassCoreIPCClientV2, payload: dict) -> None:
    msg = PublishMessage(binary_message=BinaryMessage(message=json.dumps(payload).encode("utf-8")))
    ipc.publish_to_topic(topic=RESPONSE_TOPIC, publish_message=msg)


def _handle_request(ipc: GreengrassCoreIPCClientV2, raw: bytes) -> None:
    started = time.perf_counter()
    request_id = "<unknown>"
    try:
        body = json.loads(raw.decode("utf-8"))
        request_id = body.get("request_id", "<missing>")
        input_path = body["input_path"]
        output_path = body["output_path"]
        img_format = body.get("format")

        log.info("Resizing request_id=%s in=%s out=%s", request_id, input_path, output_path)
        resize_file(input_path=input_path, output_path=output_path, img_format=img_format)
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        _publish(ipc, {
            "request_id": request_id,
            "status": "success",
            "output_path": output_path,
            "elapsed_ms": elapsed_ms,
        })
        log.info("Done request_id=%s elapsed=%.2fms", request_id, elapsed_ms)
    except Exception as exc:  # noqa: BLE001 - we publish the error back
        log.error("Failure handling request_id=%s: %s\n%s", request_id, exc, traceback.format_exc())
        _publish(ipc, {
            "request_id": request_id,
            "status": "error",
            "error": str(exc),
        })


def main() -> int:
    log.info(
        "Starting cmpe281.edge_resizer | request=%s response=%s max_inflight=%d",
        REQUEST_TOPIC, RESPONSE_TOPIC, MAX_INFLIGHT,
    )
    ipc = GreengrassCoreIPCClientV2()

    pool = ThreadPoolExecutor(max_workers=MAX_INFLIGHT, thread_name_prefix="resize")

    def on_stream_event(event: SubscriptionResponseMessage) -> None:
        try:
            payload = event.binary_message.message if event.binary_message else b""
            if not payload:
                return
            pool.submit(_handle_request, ipc, payload)
        except Exception as exc:  # noqa: BLE001
            log.error("on_stream_event failed: %s", exc)

    def on_stream_error(error: Exception) -> bool:
        log.error("Stream error: %s", error)
        return False  # do not close the stream

    def on_stream_closed() -> None:
        log.warning("Subscription stream closed.")

    _, operation = ipc.subscribe_to_topic(
        topic=REQUEST_TOPIC,
        on_stream_event=on_stream_event,
        on_stream_error=on_stream_error,
        on_stream_closed=on_stream_closed,
    )

    log.info("Subscribed to %s. Awaiting requests...", REQUEST_TOPIC)
    stop_event = threading.Event()
    try:
        stop_event.wait()
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt — shutting down.")
    finally:
        try:
            operation.close()
        except Exception:
            pass
        pool.shutdown(wait=True, cancel_futures=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
