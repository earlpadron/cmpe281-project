import boto3
import json
import os
from PIL import Image
from io import BytesIO

s3 = boto3.client('s3')

def lambda_handler(event, context):
    """ AWS Lambda function to process an image from S3. It resizes the image to 800x800 and returns the processing latency. """
    try:
        # Catch proactive warming ping
        if 'warm_ping' in event:
            print("Warming ping received. Container initialized.")
            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'Container warmed successfully.'})
            }

        # get bucket and key from the event
        bucket = event['bucket']
        key = event['key']

        # download image from S3 to memroy
        response = s3.get_object(Bucket=bucket, Key=key)
        img_data = response['Body'].read()

        # open image with Pillow
        img = Image.open(BytesIO(img_data))
        img_format = img.format # e.g., 'JPEG', 'PNG'

        # perform the exact same resize workload as the Edge
        resized_img = img.resize((800, 800), Image.Resampling.LANCZOS)

        # save the resized image to a memory buffer 
        buffer = BytesIO()
        resized_img.save(buffer, format=img_format)
        buffer.seek(0)

        # upload the resized image back to S3
        # we add 'resized/' prefix to keep things organized
        new_key = f"resized/{key}"
        s3.put_object(Bucket=bucket, Key=new_key, Body=buffer, ContentType=f"image/{img_format.lower()}")

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message' : f"Image resized successfully",
                'bucket': bucket,
                'key': new_key
            })
        }
    except Exception as e:
        print(f"Error processing image: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }