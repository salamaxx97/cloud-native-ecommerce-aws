import json
import uuid
import boto3
import os

# 1. التحقق الفوري من المتغيرات البيئية عند الـ Initialization (Fail-Fast)
SQS_QUEUE_URL = os.environ.get('SQS_QUEUE_URL')
ALLOWED_ORIGIN = os.environ.get('CORS_ORIGIN', 'https://yourdomain.com')

if not SQS_QUEUE_URL:
    raise RuntimeError("CRITICAL CONFIGURATION ERROR: SQS_QUEUE_URL environment variable is missing!")

# تهيئة الـ Client بعد التأكد من وجود المتغيرات البيئية لضمان سلامة الـ Context
sqs_client = boto3.client('sqs')

def lambda_handler(event, context):
    # إعدادات الـ CORS الصارمة للـ Production
    headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": ALLOWED_ORIGIN, 
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "POST,OPTIONS"
    }
    
    # 2. التحقق من الـ HTTP Method المتوافق مع الـ HTTP API (Payload v2)
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    
    # معالجة طلبات الـ Preflight (OPTIONS) فوراً
    if method == "OPTIONS":
        return {'statusCode': 200, 'headers': headers, 'body': ''}

    try:
        body = json.loads(event.get('body', '{}'))
        
        items = body.get("items")
        total = body.get("total")

        # 3. التدقيق الصارم على مستوى الـ Root Object
        if not isinstance(items, list) or len(items) == 0:
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'detail': 'Cart items are required and must be a non-empty list.'})
            }

        if not isinstance(total, (int, float)):
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'detail': 'Invalid total amount. Must be a numeric value.'})
            }

        # 4. الـ Deep Validation على عناصر السلة (منع الـ Poison Pills والـ Logic Exploits)
        for item in items:
            if not isinstance(item, dict):
                return {
                    'statusCode': 400,
                    'headers': headers,
                    'body': json.dumps({'detail': 'Each cart item must be an object.'})
                }

            product_id = item.get("product_id")
            quantity = item.get("quantity")

            if not isinstance(product_id, int):
                return {
                    'statusCode': 400,
                    'headers': headers,
                    'body': json.dumps({'detail': 'product_id must be integer.'})
                }

            if not isinstance(quantity, int) or quantity <= 0:
                return {
                    'statusCode': 400,
                    'headers': headers,
                    'body': json.dumps({'detail': 'quantity must be a positive integer.'})
                }

        # 5. استخراج الـ Claims الموثقة القادمة من الـ API Gateway عبر Cognito
        authorizer = event.get('requestContext', {}).get('authorizer', {})
        claims = authorizer.get('jwt', {}).get('claims', {})
        
        user_id = claims.get('sub')
        email = claims.get('email')

        if not user_id or not email:
            return {
                'statusCode': 401,
                'headers': headers,
                'body': json.dumps({'detail': 'Identity validation failed. Unauthorized request.'})
            }
        
        # 6. توليد الـ Payload النظيف وإرساله للطابور
        order_id = str(uuid.uuid4())
        message_body = {
            "order_id": order_id,
            "user_id": user_id,
            "email": email,
            "items": items,
            "total": total
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

    except json.JSONDecodeError:
        return {
            'statusCode': 400,
            'headers': headers,
            'body': json.dumps({'detail': 'Malformed JSON body.'})
        }
    except Exception as e:
        print(f"CRITICAL ERROR (Producer): {str(e)}")
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'detail': 'Internal server processing error.'})
        }