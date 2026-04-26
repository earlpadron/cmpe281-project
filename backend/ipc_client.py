"""
IPC bridge from the Edge Decision Engine (`cmpe281.decision_engine`) to the
edge-side resize worker (`cmpe281.edge_resizer`). Implements a thin
request/response pattern over Greengrass v2 local pub/sub:

    publish  -> cmpe281/edge/resize/request   (request body w/ request_id)
    subscribe<- cmpe281/edge/resize/response  (response body w/ matching id)

Each in-flight call waits on its own asyncio.Future, keyed by request_id, so
multiple concurrent FastAPI requests can share a single IPC subscription.

Bytes never travel over IPC -- both processes share the local filesystem and
exchange paths under `/tmp/cmpe281/`. This handles multi-MB images cleanly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

log = logging.getLogger("cmpe281.decision_engine.ipc")


REQUEST_TOPIC = os.environ.get("RESIZE_REQUEST_TOPIC", "cmpe281/edge/resize/request")
RESPONSE_TOPIC = os.environ.get("RESIZE_RESPONSE_TOPIC", "cmpe281/edge/resize/response")
TEMP_DIR = Path(os.environ.get("RESIZE_TEMP_DIR", "/tmp/cmpe281"))
DEFAULT_TIMEOUT_S = float(os.environ.get("RESIZE_RPC_TIMEOUT_S", "30"))


@dataclass
class ResizeResult:
    output_path: str
    elapsed_ms: float


class GreengrassResizeClient:
    """Async request/response wrapper around Greengrass IPC pub/sub."""

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ipc = None  # GreengrassCoreIPCClientV2, lazy
        self._pending: Dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self._sub_op = None
        self._started = False

    async def start(self) -> None:
        """Lazy connect + subscribe. Safe to call multiple times."""
        if self._started:
            return
        async with self._lock:
            if self._started:
                return
            from awsiot.greengrasscoreipc.clientv2 import GreengrassCoreIPCClientV2

            self._loop = asyncio.get_running_loop()
            TEMP_DIR.mkdir(parents=True, exist_ok=True)

            self._ipc = GreengrassCoreIPCClientV2()
            _, self._sub_op = self._ipc.subscribe_to_topic(
                topic=RESPONSE_TOPIC,
                on_stream_event=self._on_event,
                on_stream_error=lambda err: (log.error("IPC stream error: %s", err) or False),
                on_stream_closed=lambda: log.warning("IPC stream closed"),
            )
            self._started = True
            log.info("IPC client ready. request=%s response=%s", REQUEST_TOPIC, RESPONSE_TOPIC)

    def _on_event(self, event) -> None:
        try:
            payload = event.binary_message.message if event.binary_message else b""
            if not payload:
                return
            data = json.loads(payload.decode("utf-8"))
            request_id = data.get("request_id")
            if request_id is None or self._loop is None:
                return
            fut = self._pending.pop(request_id, None)
            if fut is None or fut.done():
                return
            self._loop.call_soon_threadsafe(fut.set_result, data)
        except Exception:  # noqa: BLE001
            log.exception("Failed to dispatch IPC response")

    async def resize(
        self,
        image_bytes: bytes,
        img_format: Optional[str] = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> bytes:
        """Send `image_bytes` to the resize worker, await the result, return resized bytes."""
        await self.start()

        request_id = str(uuid.uuid4())
        suffix = (img_format or "bin").lower()
        in_path = TEMP_DIR / f"{request_id}.in.{suffix}"
        out_path = TEMP_DIR / f"{request_id}.out.{suffix}"

        in_path.write_bytes(image_bytes)

        from awsiot.greengrasscoreipc.model import BinaryMessage, PublishMessage

        body = json.dumps({
            "request_id": request_id,
            "input_path": str(in_path),
            "output_path": str(out_path),
            "format": img_format,
        }).encode("utf-8")

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = fut

        try:
            t0 = time.perf_counter()
            self._ipc.publish_to_topic(
                topic=REQUEST_TOPIC,
                publish_message=PublishMessage(binary_message=BinaryMessage(message=body)),
            )

            try:
                response = await asyncio.wait_for(fut, timeout=timeout_s)
            except asyncio.TimeoutError as exc:
                self._pending.pop(request_id, None)
                raise TimeoutError(f"resize worker did not respond in {timeout_s}s") from exc

            if response.get("status") != "success":
                raise RuntimeError(response.get("error", "resize worker reported error"))

            out_bytes = Path(response["output_path"]).read_bytes()
            log.info(
                "IPC resize ok request_id=%s worker=%.2fms total=%.2fms",
                request_id, response.get("elapsed_ms", -1.0), (time.perf_counter() - t0) * 1000,
            )
            return out_bytes
        finally:
            for p in (in_path, out_path):
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass


_singleton: Optional[GreengrassResizeClient] = None


def get_client() -> GreengrassResizeClient:
    global _singleton
    if _singleton is None:
        _singleton = GreengrassResizeClient()
    return _singleton
