import argparse
import csv
import statistics
import time
from datetime import datetime
from pathlib import Path

import requests


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    index = int((p / 100) * len(values)) - 1
    index = max(0, min(index, len(values) - 1))
    return values[index]


def run_load_test(url, image_dir, mode, output_csv):
    base = Path(image_dir).expanduser().resolve()
    if not base.is_dir():
        raise RuntimeError(
            f"Image directory does not exist: {base}\n"
            f"Example from repo root: --image-dir backend/test_images"
        )
    # Flat directory of files only (skip subfolders); JPEG load tests against /resize
    image_paths = sorted(
        p for p in base.glob("*") if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg"}
    )

    if not image_paths:
        raise RuntimeError(
            f"No .jpg/.jpeg files found in {base}\n"
            f"Put images there or pass --image-dir path/to/folder "
            f"(repo samples: backend/test_images)"
        )

    rows = []

    for image_path in image_paths:
        with open(image_path, "rb") as f:
            start = time.perf_counter()
            try:
                response = requests.post(
                    url,
                    files={"file": (image_path.name, f, "image/jpeg")},
                    timeout=120,
                )
                latency_ms = (time.perf_counter() - start) * 1000

                route = response.headers.get("X-Routing-Decision", "UNKNOWN")

                rows.append({
                    "timestamp": datetime.now().isoformat(),
                    "mode": mode,
                    "filename": image_path.name,
                    "file_size_bytes": image_path.stat().st_size,
                    "status_code": response.status_code,
                    "route": route,
                    "latency_ms": round(latency_ms, 3),
                    "success": response.status_code == 200,
                })

                print(f"{image_path.name}: {response.status_code}, {route}, {latency_ms:.2f} ms")

            except Exception as e:
                latency_ms = (time.perf_counter() - start) * 1000
                rows.append({
                    "timestamp": datetime.now().isoformat(),
                    "mode": mode,
                    "filename": image_path.name,
                    "file_size_bytes": image_path.stat().st_size,
                    "status_code": "ERROR",
                    "route": "ERROR",
                    "latency_ms": round(latency_ms, 3),
                    "success": False,
                    "error": str(e),
                })
                print(f"{image_path.name}: ERROR, {e}")

    output_csv = Path(output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "timestamp",
        "mode",
        "filename",
        "file_size_bytes",
        "status_code",
        "route",
        "latency_ms",
        "success",
        "error",
    ]

    with open(output_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    successful_latencies = [r["latency_ms"] for r in rows if r["success"]]

    print("\n--- Summary ---")
    print(f"Mode: {mode}")
    print(f"Requests: {len(rows)}")
    print(f"Successful: {len(successful_latencies)}")

    if successful_latencies:
        print(f"Average latency: {statistics.mean(successful_latencies):.2f} ms")
        print(f"Median latency: {statistics.median(successful_latencies):.2f} ms")
        print(f"P95 latency: {percentile(successful_latencies, 95):.2f} ms")

    print(f"Saved results to: {output_csv}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000/resize")
    parser.add_argument(
        "--image-dir",
        default="backend/test_images",
        help="Folder containing .jpg/.jpeg files (from repo root try backend/test_images)",
    )
    parser.add_argument("--mode", required=True, choices=["ml", "edge", "cloud"])
    parser.add_argument("--output", required=True)

    args = parser.parse_args()

    run_load_test(
        url=args.url,
        image_dir=args.image_dir,
        mode=args.mode,
        output_csv=args.output,
    )