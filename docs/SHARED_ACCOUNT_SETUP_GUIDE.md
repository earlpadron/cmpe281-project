# Shared AWS Account: Backend Infrastructure Setup Guide

Hi Team! To fully migrate our Intelligent Edge-Cloud Task Placement Framework to the shared AWS account (ID: `549955691205`), we need to provision the core backend resources.

Since we are following the Principle of Least Privilege, please execute these steps as the AWS Administrator.

---

## Step 1: Create the S3 Data Lake (For Edge-Cloud Payload Transfer)
This bucket acts as the primary data lake for our architecture. It stores the raw images uploaded by the frontend and the processed thumbnails returned by Lambda.

1.  Navigate to **S3** -> **Create bucket**.
2.  **Bucket Name:** `cmpe281-shared-benchmark-data-ep` *(Note: Earl has already configured the Python backend to point to this specific name).*
3.  **AWS Region:** US East (N. Virginia) `us-east-1`.
4.  **Object Ownership:** ACLs disabled (recommended).
5.  **Block Public Access:** **Keep this CHECKED (Block all public access).** Security is paramount; all access will be handled via IAM roles.
6.  **Bucket Versioning:** Disable.
7.  **Default Encryption:** Server-side encryption with Amazon S3 managed keys (SSE-S3).
8.  Click **Create bucket**.

---

## Step 2: Create the Lambda Execution Role (IAM)
AWS Lambda needs explicit permission to execute code, write logs to CloudWatch, and read/write to the S3 bucket we just created.

1.  Navigate to **IAM** -> **Roles** -> **Create role**.
2.  **Trusted Entity Type:** Select **AWS service**.
3.  **Use Case:** Select **Lambda** -> Next.
4.  **Add Permissions (AWS Managed):**
    *   Search for and select: `AWSLambdaBasicExecutionRole` *(This allows Lambda to write billing metrics to CloudWatch).*
    *   Click Next.
5.  **Role Details:**
    *   **Role name:** `cmpe281-shared-lambda-role`
    *   Click **Create role**.

6.  **Add Permissions (Custom S3 Access):**
    *   Find the newly created `cmpe281-shared-lambda-role` and click on it.
    *   Under "Permissions policies", click **Add permissions** -> **Create inline policy**.
    *   Switch to the **JSON** tab and paste the following strict, Least-Privilege policy:

    ```json
    {
      "Version": "2012-10-17",
      "Statement": [
        {
          "Effect": "Allow",
          "Action": [
            "s3:GetObject",
            "s3:PutObject"
          ],
          "Resource": [
            "arn:aws:s3:::cmpe281-shared-benchmark-data-ep",
            "arn:aws:s3:::cmpe281-shared-benchmark-data-ep/*"
          ]
        }
      ]
    }
    ```
    *   Click Next, name the policy `SharedS3Access`, and click **Create policy**.

---

## Step 3: Deploy the AWS Lambda Function (Cloud Compute)
This is the serverless function that handles the heavy image resizing when the Edge device (Raspberry Pi) decides to route traffic to the cloud.

*Note: Because our `.gitignore` blocks `.zip` files, you must generate the deployment package locally on your machine before uploading it to the shared AWS account.*

**Local Packaging Instructions (Run in your terminal from the project root):**
```bash
mkdir -p lambda_pkg
# Download the Linux-compiled version of Pillow
pip install --platform manylinux2014_x86_64 --target=lambda_pkg --implementation cp --python-version 3.12 --only-binary=:all: Pillow
# Copy the Lambda function script
cp cloud/lambda_function.py lambda_pkg/
# Zip the contents
cd lambda_pkg && zip -r ../lambda_deployment.zip . && cd ..
```

**AWS Console Deployment:**
1.  Navigate to **Lambda** -> **Create function**.
2.  Select **Author from scratch**.
3.  **Function name:** `cmpe281-shared-image-resizer` *(Note: Earl has already configured the Python backend to invoke this specific name).*
4.  **Runtime:** Python 3.12.
5.  **Permissions:** Expand "Change default execution role", select **Use an existing role**, and choose the `cmpe281-shared-lambda-role` created in Step 2.
6.  Click **Create function**.

**Configuration & Code Upload:**
7.  In the function dashboard, go to the **Code** tab.
8.  Click **Upload from** -> **.zip file** and upload the `lambda_deployment.zip`.
9.  Go to the **Configuration** tab -> **General configuration** -> **Edit**.
10. Set **Memory** to `1024 MB`.
11. Set **Timeout** to `15 sec`.
12. Click **Save**.

---

Once these three steps are complete, the cloud infrastructure is fully deployed! Earl's local `main.py` and the Raspberry Pi's Greengrass deployment will now successfully route data through the shared AWS account.