# AWS Infrastructure Setup Guide: Comprehensive Manual & Programmatic Deployment

This guide provides a detailed walkthrough for setting up the **Intelligent Edge-Cloud Task Placement Framework** on AWS. It covers initial environment setup, manual console configurations, and programmatic CLI deployment.

---

## Part 0: Initial Environment Setup (Connecting your Machine)

Before you can run `benchmark.py` or use the CLI, your local machine needs "Permission" to talk to your AWS account.

### 1. Install the AWS CLI
*   **macOS:** `brew install awscli`
*   **Windows:** Download and run the [64-bit MSI installer](https://awscli.amazonaws.com/AWSCLIV2.msi).
*   **Linux:** `sudo apt-get install awscli` (or use the [bundled installer](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)).

### 2. Authenticate your Terminal
1.  Go to the **AWS Console > IAM > Users > [Your User] > Security Credentials**.
2.  Click **Create access key** and select **Command Line Interface (CLI)**.
3.  Copy your **Access Key ID** and **Secret Access Key**.
4.  In your terminal, run:
    ```bash
    aws configure
    ```
5.  Enter your keys when prompted. Set **Default region name** to `us-east-1` and **Default output format** to `json`.

---

## Part 1: Manual Setup via AWS Console (The "Web Browser" Method)

Follow these exact steps to ensure `benchmark.py` can collect accurate telemetry.

### 1. Create the IAM Identity (Permissions)
*   **Navigate to:** IAM > Roles > Create role.
*   **Step 1 (Select Trusted Entity):** Choose **AWS Service** and select **Lambda**. Click Next.
*   **Step 2 (Add Permissions):** 
    - Search for `AWSLambdaBasicExecutionRole` and check it. (This allows logging).
    - Click Next.
*   **Step 3 (Name & Review):** Name the role `cmpe281-lambda-role`. Click Create role.
*   **Step 4 (Add S3 Access):**
    - Click on your new role (`cmpe281-lambda-role`).
    - Click **Add permissions > Create inline policy**.
    - Click the **JSON** tab and paste the contents of `s3-policy.json`.
    - Name it `S3DataAccess` and click Create policy.

### 2. Create the S3 Bucket (Storage)
*   **Navigate to:** S3 > Create bucket.
*   **Bucket Name:** Provide a unique name (e.g., `cmpe281-data-[your-name]`).
*   **Region:** Select `us-east-1` (N. Virginia).
*   **Settings:** Leave everything else as default (Block all public access should stay ON).
*   **Result:** Copy this bucket name; you will need it for `benchmark.py`.

### 3. Create and Configure the Lambda Function (Compute)
*   **Navigate to:** Lambda > Create function.
*   **Basics:** 
    - Name: `cmpe281-image-resizer`.
    - Runtime: **Python 3.12** (or most recent).
    - Permissions: Change "Default execution role" to **Use an existing role** and select `cmpe281-lambda-role`.
*   **Code Upload:**
    - On your machine, zip `lambda_function.py` and the `PIL` library (see Part 2 for packaging instructions).
    - In the Lambda Console, click **Upload from > .zip file**.
*   **Critical Settings (Configuration Tab):**
    - **General Configuration:** Set **Memory** to 1024 MB and **Timeout** to 15 seconds. (Higher memory = faster processing and better telemetry).
    - **Concurrency:** Ensure "Unreserved account concurrency" is available.

---

## Part 2: Programmatic Setup via AWS CLI (The "Terminal" Method)

If you prefer to automate the steps above, use these commands. Ensure you have completed **Part 0** first.

### 1. Provision IAM & S3
```bash
# 1. Create the role
aws iam create-role --role-name cmpe281-lambda-role --assume-role-policy-document file://trust-policy.json

# 2. Attach logging and S3 permissions
aws iam attach-role-policy --role-name cmpe281-lambda-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam put-role-policy --role-name cmpe281-lambda-role --policy-name S3Access --policy-document file://s3-policy.json

# 3. Create the bucket
BUCKET_NAME="cmpe281-benchmark-$(date +%s)"
aws s3 mb s3://$BUCKET_NAME --region us-east-1
```

### 2. Package and Deploy Lambda
```bash
# Prepare the deployment package
mkdir -p lambda_pkg
pip install Pillow -t lambda_pkg/
cp lambda_function.py lambda_pkg/
cd lambda_pkg && zip -r ../lambda_deployment.zip . && cd ..

# Deploy the function
ROLE_ARN=$(aws iam get-role --role-name cmpe281-lambda-role --query 'Role.Arn' --output text)
aws lambda create-function \
    --function-name cmpe281-image-resizer \
    --runtime python3.12 \
    --role $ROLE_ARN \
    --handler lambda_function.lambda_handler \
    --zip-file fileb://lambda_deployment.zip \
    --memory-size 1024 \
    --timeout 15
```

---

## Part 3: Running the Benchmark

Once the infrastructure is ready, update the bottom of your `benchmark.py` file:

```python
if __name__ == "__main__":
    # Replace these with your actual names from Part 1 or Part 2
    bench = BenchmarkFramework(
        bucket_name='your-unique-bucket-name', 
        lambda_name='cmpe281-image-resizer'
    )
    bench.run_benchmark(limit=100) 
```

### Telemetry Logic: How it Works
1.  **Network RTT:** Script uses `s3.head_bucket()` to measure base round-trip time.
2.  **Uplink Bandwidth:** Calculated by dividing the image size by the `s3.put_object()` duration.
3.  **Cloud Latency:** The script invokes Lambda with `LogType='Tail'`, which returns the execution logs.
4.  **Cost & Cold Starts:** The script parses the "Billed Duration" and "Init Duration" from the logs to calculate the exact USD cost and mark if a Cold Start occurred.

---

## Part 4: Updating your Code (Redeployment)

If you modify `lambda_function.py` locally and need to push those changes to AWS, follow these steps.

### Method A: Full Re-package (Safest)
Use this if you added new libraries or want a clean build.
```bash
cp lambda_function.py lambda_pkg/
cd lambda_pkg && zip -r ../lambda_deployment.zip . && cd ..

aws lambda update-function-code \
    --function-name cmpe281-image-resizer \
    --zip-file fileb://lambda_deployment.zip
```

### Method B: Quick Update (Fastest)
Use this if you **only** modified the text in `cloud/lambda_function.py`.
```bash
# Update the existing zip with the new script
cd cloud && zip -g ../lambda_deployment.zip lambda_function.py && cd ..

# Push to AWS
aws lambda update-function-code \
    --function-name cmpe281-image-resizer \
    --zip-file fileb://lambda_deployment.zip
```
e cmpe281-image-resizer \
    --zip-file fileb://lambda_deployment.zip
```
