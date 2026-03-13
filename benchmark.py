import os
import time
import json
import psutil
import boto3
import pandas as pd
import base64
import re
from PIL import Image
from io import BytesIO
from datetime import datetime, timezone

class BenchmarkFramework:
    def __init__(self, region_name='us-east-1', bucket_name=None, lambda_name=None):
        """Initialize the AWS clients and benchmarking configuration."""
        self.s3 = boto3.client('s3', region_name=region_name)
        self.lambda_client = boto3.client('lambda', region_name=region_name)
        self.cloudwatch = boto3.client('logs', region_name=region_name)

        self.bucket_name = bucket_name
        self.lambda_name = lambda_name  

        # store the results of the benchmark
        self.results = []

    def get_hardware_metrics(self):
        """Capture a snapshot of current CPU and Memory utilization."""
        metrics = {
            "edge_cpu_utilization": psutil.cpu_percent(interval=None),
            "edge_memory_utilization": psutil.virtual_memory().percent
        }
        return metrics
    
    def benchmark_edge(self, image_path):
        """ Simulate the edge image processing workload and measure executing time.
        
        Args:
            image_path (str): The local path to the image to be processed.
        
        Returns:
            A dictionary containing the execution time and file details.
        """
        # get original file size and open image
        file_size_bytes = os.path.getsize(image_path)
        img = Image.open(image_path)
        img_format = img.format # e.g., 'JPEG', 'PNG'

        # start the timer
        start_time = time.perf_counter()

        # perform the processing task : resize the image to 800x800
        resized_img = img.resize((800, 800), Image.Resampling.LANCZOS)

        # force computation by simulating save to memory
        buffer = BytesIO()
        resized_img.save(buffer, format=img_format)

        # stop timer and calculate latency in milliseconds
        end_time = time.perf_counter()
        latency_ms = (end_time - start_time) * 1000

        # store the result
        result = {
            'image_format': img_format,
            'image_size_bytes': file_size_bytes,
            'edge_total_latency_ms': latency_ms
        }
        self.results.append(result)
        return result
    
    def benchmark_cloud(self, image_path):
        """Measure the total round-trip latency of processing an image via AWS Lambda, including cost and cold starts."""
        filename = os.path.basename(image_path)
        file_size_bytes = os.path.getsize(image_path)
        
        # 1. Measure Network RTT (Round Trip Time) via a lightweight AWS API call
        # S3 head_bucket provides an accurate RTT to the specific storage region
        start_rtt = time.perf_counter()
        try:
            self.s3.head_bucket(Bucket=self.bucket_name)
        except Exception:
            pass # ignore if bucket doesn't exist yet or permissions issue during RTT check
        rtt_ms = (time.perf_counter() - start_rtt) * 1000

        # 2. Upload the image to S3 (simulate the edge uploading to cloud) & calculate Uplink Bandwidth
        start_upload = time.perf_counter()
        with open(image_path, 'rb') as data:
            self.s3.put_object(Bucket=self.bucket_name, Key=filename, Body=data)
        upload_time_s = time.perf_counter() - start_upload
        
        # Bandwidth in kbps (Kilobits per second)
        uplink_bandwidth_kbps = (file_size_bytes * 8 / 1000) / upload_time_s if upload_time_s > 0 else 0

        # 3. Invoke the lambda function and measure the latency
        start_time = time.perf_counter()

        response = self.lambda_client.invoke(
            FunctionName=self.lambda_name,
            InvocationType='RequestResponse',
            LogType='Tail', # Request logs in the response to parse Billed Duration and Cold Starts immediately
            Payload=json.dumps({
                'bucket': self.bucket_name, 
                'key': filename})
        )

        end_time = time.perf_counter()
        total_latency_ms = (end_time - start_time) * 1000
        request_id = response['ResponseMetadata']['RequestId']

        # 4. Parse CloudWatch Logs from the 'Tail' response
        log_result = ""
        if 'LogResult' in response:
            log_result = base64.b64decode(response['LogResult']).decode('utf-8')
        
        # Default fallback values
        billed_duration_ms = 0.0
        memory_size_mb = 128.0
        cold_start_indicator = 0

        # Regex parsing for AWS Lambda REPORT format
        billed_duration_match = re.search(r'Billed Duration: (\d+) ms', log_result)
        if billed_duration_match:
            billed_duration_ms = float(billed_duration_match.group(1))
            
        memory_size_match = re.search(r'Memory Size: (\d+) MB', log_result)
        if memory_size_match:
            memory_size_mb = float(memory_size_match.group(1))
            
        init_duration_match = re.search(r'Init Duration: ([\d.]+) ms', log_result)
        if init_duration_match:
            cold_start_indicator = 1
            
        # 5. Programmatic Cost Calculation (AWS x86 Lambda Pricing)
        # Current AWS US-East-1 pricing for x86: $0.20 per 1M requests, $0.0000166667 per GB-second
        PRICE_PER_GB_SEC = 0.0000166667
        PRICE_PER_REQUEST = 0.0000002
        
        gb_seconds = (billed_duration_ms / 1000.0) * (memory_size_mb / 1024.0)
        estimated_cost_usd = PRICE_PER_REQUEST + (gb_seconds * PRICE_PER_GB_SEC)

        return {
            "network_rtt_ms": rtt_ms,
            "estimated_uplink_bandwidth_kbps": uplink_bandwidth_kbps,
            "cloud_total_latency_ms": total_latency_ms,
            "cloud_billed_duration_ms": billed_duration_ms,
            "cloud_memory_allocation_mb": memory_size_mb,
            "cloud_cold_start_indicator": cold_start_indicator,
            "estimated_cloud_cost_usd": estimated_cost_usd,
            "cloud_request_id": request_id
        }

    
    def run_benchmark(self, images_dir="images", limit=1000):
        """Run the benchmarking process by iterating through the local images dataset collected from Unsplash"""

        # get list of valid images
        all_images = [f for f in os.listdir(images_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]

        # limit the request number of images
        images_to_process = all_images[:limit]
        
        print(f"Starting benchmark on {len(images_to_process)} images...")

        for i, filename in enumerate(images_to_process):
            img_path = os.path.join(images_dir, filename)
            print(f"[{i+1}/{len(images_to_process)}] Benchmarking {filename}...")
            
            try:
                # capture the hardware state before processing (important for ML feature)
                hw_metrics = self.get_hardware_metrics()

                # run edge benchmark (adds a new record to self.results)
                self.benchmark_edge(img_path)

                # update the record within the results list with hardware metrics
                self.results[-1].update(hw_metrics)

                # run cloud benchmark
                cloud_result = self.benchmark_cloud(img_path)
                
                # integrate the cloud metrics into the final result record
                self.results[-1].update(cloud_result)
                
                final_result = self.results[-1]
                print(f"  -> Edge Latency: {final_result.get('edge_total_latency_ms', 0):.2f}ms | Cloud Latency: {final_result.get('cloud_total_latency_ms', 0):.2f}ms")
                print(f"  -> Cloud Cost: ${final_result.get('estimated_cloud_cost_usd', 0):.6f} | Cold Start: {final_result.get('cloud_cold_start_indicator', 0)}")

            except Exception as e:
                print(f"Error benchmarking {filename}: {e}")
                continue

        # summarize results
        if self.results:
            df = pd.DataFrame(self.results)
            print("\n--- Benchmarking Summary ---")
            print(df.describe())
        
            # save to CSV for future ML training
            output_file = f"benchmark_results_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
            df.to_csv(output_file, index=False)
            print(f"\nResults saved to {output_file}")

        return self.results

if __name__ == "__main__":
    # Note: Replace 'your-bucket-name' and 'your-lambda-function-name' with actual AWS resources
    # Ensure AWS credentials are set up (e.g., via aws configure)
    bench = BenchmarkFramework(bucket_name='cmpe281-benchmark-data-a81aa9e4', lambda_name='cmpe281-image-resizer')
    print(f"Initial Hardware Metrics: {bench.get_hardware_metrics()}")
    bench.run_benchmark(limit=10) # Run a quick test with 10 images
