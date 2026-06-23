import json
import uuid
import boto3
import os

sqs_client = boto3.client('sqs')
SQS_QUEUE_URL = os.environ.get('SQS_QUEUE_URL')

def lambda_handler(event, context):
    headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*", 
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "POST,OPTIONS"
    }
    
    if event.get('httpMethod') == 'OPTIONS' or event.get('requestContext', {}).get('http', {}).get('method') == 'OPTIONS':
        return {'statusCode': 200, 'headers': headers, 'body': ''}

    try:
        body = json.loads(event.get('body', '{}'))
        
        if 'items' not in body or 'total' not in body:
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'detail': 'بيانات السلة أو الإجمالي ناقصة'})
            }

        # ✅ قراءة الـ Claims بمرونة لتتوافق مع REST API و HTTP API معاً دون تعديل الكود مستقبلاً
        authorizer = event.get('requestContext', {}).get('authorizer', {})
        claims = authorizer.get('claims') or authorizer.get('jwt', {}).get('claims', {})
        
        user_id = claims.get('sub', 'anonymous_user') 
        email = claims.get('email', body.get('email', 'unknown@mail.com'))

        order_id = str(uuid.uuid4())

        message_body = {
            "order_id": order_id,
            "user_id": user_id,
            "email": email,
            "items": body['items'],
            "total": body['total']
        }

        sqs_client.send_message(
            QueueUrl=SQS_QUEUE_URL,
            MessageBody=json.dumps(message_body)
        )

        return {
            'statusCode': 202,
            'headers': headers,
            'body': json.dumps({
                "message": "Order received and is being processed",
                "status": "PENDING",
                "order_id": order_id
            })
        }
    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'detail': 'حدث خطأ في نظام الـ Checkout الخارجي'})
        }