# Intelligent Edge-Cloud Task Placement Framework

This project implements a hybrid edge-cloud serverless framework that dynamically routes image processing tasks between a Raspberry Pi (Edge) and AWS Lambda (Cloud) based on Machine Learning predictions.

## Current Status: Phase 1 (Data Collection & Benchmarking)
We are currently gathering empirical data to train our ML models. This involves running an image resizing workload on both local hardware and AWS to measure latency, cost, and hardware utilization.

## Project Structure
- `benchmark.py`: The main script that runs the benchmarking loop (Edge vs. Cloud).
- `lambda_function.py`: The code running in AWS Lambda that performs the cloud resizing.
- `download_dataset.py`: Script to download 1,000 real-world images from the Unsplash dataset.
- `AWS_SETUP_GUIDE.md`: A detailed mapping of how to set up the AWS infrastructure.
- `PI_SETUP_GUIDE.md` : Might be helpful when running the benchmark on the Raspberry PI
- `images/`: Local directory containing the Unsplash dataset.

## Prerequisites
1. **Python 3.10+**
2. **AWS CLI** configured with `aws configure`.
3. **Python Libraries:**
   ```bash
   pip install boto3 pillow psutil pandas tqdm requests
   ```

## Getting Started

### 1. Download the Dataset
Download the Lite Dataset[~700MB compressed, ~1GB raw] from this github link: https://github.com/unsplash/datasets?tab=readme-ov-file. Place the uncompressed file inside the root folder (cmpe281-project). We're specifically using the "photos.csv000" file that contains the links to each image. 

If the `images/` directory is empty, run the download script to pull 1,000(you can choose if you want less or more in "download.dataset", but must be less than 25,000) images from the Unsplash Lite dataset:
```bash
python3 download_dataset.py
```

### 2. Setup AWS Infrastructure
Follow the [AWS_SETUP_GUIDE.md](./AWS_SETUP_GUIDE.md) to create the necessary S3 bucket and Lambda function.

### 3. Configure the Benchmark
Open `benchmark.py` and update the bottom of the file with your specific AWS resources:
```python
if __name__ == "__main__":
    bench = BenchmarkFramework(
        bucket_name='your-unique-bucket-name', 
        lambda_name='cmpe281-image-resizer'
    )
```

### 4. Run the Benchmark
Execute the benchmark to generate the CSV dataset:
```bash
python3 benchmark.py
```
This will process images and save the results to a file named `benchmark_results_[TIMESTAMP].csv`. This CSV will be used in Phase 2 for ML training.

## Authors
- Bryan Cortes
- Earl Padron
- Irwin Salamanca
- Matthew Tang
