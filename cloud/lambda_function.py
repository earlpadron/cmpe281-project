import boto3
import json
from io import BytesIO

from PIL import Image

# `resize_lib` is the shared resize module. It lives at
# `backend/lib/resize.py` and is copied into the Lambda zip + the Greengrass
# component artifact at build time so cloud and edge run identical code.
try:
    from resize_lib import resize_bytes
except ImportError:  # pragma: no cover - local/dev fallback
    resize_bytes = None

s3 = boto3.client('s3')


def lambda_handler(event, context):
    """AWS Lambda image resizer. Routes a put_object payload through the shared resize core."""
    try:
        if 'warm_ping' in event:
            print("Warming ping received. Container initialized.")
            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'Container warmed successfully.'})
            }

        bucket = event['bucket']
        key = event['key']

        response = s3.get_object(Bucket=bucket, Key=key)
        img_data = response['Body'].read()

        if resize_bytes is not None:
            img_format = Image.open(BytesIO(img_data)).format
            buffer_bytes = resize_bytes(img_data, img_format=img_format)
        else:
            img = Image.open(BytesIO(img_data))
            img_format = img.format
            resized = img.resize((800, 800), Image.Resampling.LANCZOS)
            buf = BytesIO()
            resized.save(buf, format=img_format)
            buffer_bytes = buf.getvalue()

        new_key = f"resized/{key}"
        s3.put_object(
            Bucket=bucket,
            Key=new_key,
            Body=buffer_bytes,
            ContentType=f"image/{img_format.lower()}",
        )

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