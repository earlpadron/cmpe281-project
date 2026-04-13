# Understanding Programmatic Setup: AWS CLI vs. Console

This document explains the transition from manual "point-and-click" cloud management to **Infrastructure as Code (IaC)** using the AWS CLI.

---

## 1. The Core Concept: "The Skin vs. The Engine"
Everything you see in the **AWS Management Console** (the website) is just a "skin" or a user interface. When you click a button in your browser, it sends a JSON-formatted request to an AWS API. 

The **AWS CLI** (Command Line Interface) allows us to send those same messages directly from our terminal. This is "Programmatic Setup."

---

## 2. Behind the Scenes of the Deployment Files
Here is how the specific files in this project act as instructions for the AWS "Engine":

### A. `trust-policy.json` (The Identity Card)
*   **Manual Way:** Clicking "Create Role" and selecting "Lambda" from a list.
*   **CLI Way:** `aws iam create-role --assume-role-policy-document file://trust-policy.json`
*   **What happens:** The CLI reads the JSON and tells AWS: "Create a new identity that the Lambda service is allowed to wear."

### B. `s3-policy.json` (The Keycard)
*   **Manual Way:** Searching for policies and clicking "Attach."
*   **CLI Way:** `aws iam put-role-policy --policy-document file://s3-policy.json`
*   **What happens:** This JSON defines exactly which "rooms" (S3 buckets) the identity can enter. The CLI "taps" this keycard against the role we just created.

### C. `lambda_deployment.zip` (The Cargo)
*   **Manual Way:** Clicking "Upload from .zip" in the browser.
*   **CLI Way:** `aws lambda create-function --zip-file fileb://lambda_deployment.zip`
*   **What happens:** The `fileb://` prefix tells the CLI to send the raw binary data. Your code is physically moved from your local disk to AWS's data centers.

### D. `.bucket_name` (The Memory)
*   **Manual Way:** Writing the bucket name on a sticky note so you don't forget it.
*   **CLI Way:** `echo $BUCKET_NAME > .bucket_name`
*   **What happens:** We save the randomly generated name into a hidden file. This allows our other scripts (like `benchmark.py`) to automatically "read" the name without a human having to type it in.

---

## 3. Why Use the CLI? (IaC vs. Manual)

| Feature | The "Old Way" (Console) | The "IaC Way" (CLI/Scripts) |
| :--- | :--- | :--- |
| **Speed** | 5-10 minutes of clicking. | 10 seconds of script execution. |
| **Accuracy** | Easy to forget a checkbox or a timeout setting. | 100% identical every time you run it. |
| **Portability** | Hard to explain to a teammate how you did it. | Give them the script; they are up and running instantly. |
| **Scaling** | Hard to manage 100 functions. | One loop in a script can manage 1000 functions. |

---

## 4. How it Matches the Setup Guide
The `AWS_SETUP_GUIDE.md` (Part 2) uses these exact programmatic principles. When you run the commands in that guide, you aren't just "running code"—you are **provisioning infrastructure**. 

By defining your environment in `trust-policy.json` and `s3-policy.json`, you have successfully turned a complex cloud setup into a repeatable, version-controlled asset.
