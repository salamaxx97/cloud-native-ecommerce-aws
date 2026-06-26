import json
import os
import boto3
import psycopg2
import socket
from botocore.config import Config

# 1. التحقق الفوري من المتغيرات البيئية عند الـ Initialization (Fail-Fast)
SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN')
SECRET_NAME = os.environ.get('SECRET_NAME')
DB_HOST = os.environ.get('DB_HOST')
DB_NAME = os.environ.get('DB_NAME')

missing_vars = [var_name for var_name, var_val in {
    "SNS_TOPIC_ARN": SNS_TOPIC_ARN,
    "SECRET_NAME": SECRET_NAME,
    "DB_HOST": DB_HOST,
    "DB_NAME": DB_NAME
}.items() if not var_val]

if missing_vars:
    raise RuntimeError(f"CRITICAL CONFIGURATION ERROR: Missing environment variables: {', '.join(missing_vars)}")


# إعداد مهلة قصيرة للاتصال والقراءة لمنع التعليق
custom_config = Config(
    connect_timeout=2,  # لو مقدرش يلقط الخدمة في ثانيتين يفصل
    read_timeout=3,     # لو مفيش رد في خلال 3 ثواني يفصل
    retries={'max_attempts': 2} # يعيد المحاولة بكونكشن جديد
)

# تمرير الـ Config للـ Clients
sns_client = boto3.client('sns', config=custom_config)
secrets_client = boto3.client('secretsmanager', config=custom_config)

# متغير عالمي للاحتفاظ باتصال قاعدة البيانات بين الـ Warm Starts
conn = None

def get_db_credentials():
    """جلب بيانات الدخول الآمنة من Secrets Manager"""
    try:
        response = secrets_client.get_secret_value(SecretId=SECRET_NAME)
        secret = json.loads(response['SecretString'])
        return secret.get('username'), secret.get('password')
    except Exception as e:
        print(f"CRITICAL: Secrets Manager retrieval failed: {str(e)}")
        raise

def get_db_connection():
    """إنشاء أو إعادة استخدام اتصال قاعدة البيانات"""
    global conn
    if conn is None or conn.closed != 0:
        print("Connecting to RDS PostgreSQL...")
        db_user, db_pass = get_db_credentials()
        print("DB_HOST =", DB_HOST)

        ip = socket.gethostbyname(DB_HOST)

        print("DB_IP =", ip)
        conn = psycopg2.connect(
            host=DB_HOST,
            database=DB_NAME,
            user=db_user,
            password=db_pass,
            port=5432,
            connect_timeout=5
        )
    return conn

def lambda_handler(event, context):
    db_conn = None
    cursor = None
    
    # لستة لتسجيل الرسائل الفاشلة فقط بدلاً من إسقاط الـ Batch بالكامل
    batch_item_failures = []

    try:
        db_conn = get_db_connection()
        cursor = db_conn.cursor()
    except Exception as db_init_error:
        print(f"CRITICAL: Database connection initialization failed: {str(db_init_error)}")
        # إذا فشل الاتصال بالداتابيز تماماً، نترك الـ SQS يعيد الـ Batch كاملاً لاحقاً
        raise db_init_error

    # معالجة الرسائل القادمة في الـ Batch
    for record in event.get('Records', []):
        message_id = record.get('messageId')
        try:
            payload = json.loads(record['body'])
            order_id = payload['order_id']
            user_id = payload['user_id']
            total = payload['total']
            email = payload['email']
            items = payload.get('items', [])

            # 2. إدخال الطلب الرئيسي (مأمن تماماً ضد الـ SQL Injection والـ Duplication)
            cursor.execute(
                """
                INSERT INTO orders (id, user_id, total, status)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING;
                """,
                (order_id, user_id, total, 'COMPLETED')
            )

            # 3. إدخال تفاصيل المنتجات داخل الطلب
            for item in items:
                cursor.execute(
                    """
                    INSERT INTO order_items (order_id, product_id, quantity)
                    VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING;
                    """,
                    (order_id, item['product_id'], item['quantity'])
                )

            # تثبيت المعاملة (Transaction) الخاصة بهذه الرسالة بنجاح
            db_conn.commit()
            print(f"SUCCESS: Order {order_id} saved.")

            # 4. بث حدث النجاح عبر SNS لإرسال الإيميلات
            sns_client.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject=f"Order Confirmed #{order_id[:8]}",
                Message=json.dumps({
                    "order_id": order_id,
                    "user_id": user_id,
                    "email": email,
                    "total": total,
                    "status": "COMPLETED"
                })
            )

        except Exception as record_error:
            # عمل Rollback مخصص لهذه الرسالة الفاشلة فقط لعدم تلويث باقي الطلبات في الـ Batch
            if db_conn:
                db_conn.rollback()
            print(f"ERROR: Failed processing message {message_id}. Reason: {str(record_error)}")
            
            # تسجيل المعرف الفريد للرسالة الفاشلة عشان SQS تعملها Retry لوحدها
            batch_item_failures.append({"itemIdentifier": message_id})

    # إغلاق الـ Cursor بأمان في نهاية الـ Batch
    if cursor:
        cursor.close()

    # 5. إرجاع تقرير بالرسائل الفاشلة (إن وجدت) لـ AWS SQS
    return {"batchItemFailures": batch_item_failures}