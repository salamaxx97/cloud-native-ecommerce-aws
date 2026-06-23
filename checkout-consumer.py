import json
import os
import boto3
import psycopg2

sns_client = boto3.client('sns')
SNS_TOPIC_ARN = os.environ.get('SNS_TOPIC_ARN')

# جلب البيانات من الـ Env Variables (بما فيهم الباسورد اللي متباصي من Secrets Manager)
DB_HOST = os.environ.get('DB_HOST')
DB_USER = os.environ.get('DB_USER')
DB_PASS = os.environ.get('DB_PASS')  # الـ Secret هيقرا هنا كـ نص عادي فوراً
DB_NAME = os.environ.get('DB_NAME')

# متغير عالمي لإعادة استخدام الاتصال بقاعدة البيانات (Connection Reuse)
conn = None

def get_db_connection():
    global conn
    if conn is None or conn.closed != 0:
        conn = psycopg2.connect(
            host=DB_HOST, 
            database=DB_NAME, 
            user=DB_USER, 
            password=DB_PASS, 
            port=5432
        )
    return conn

def lambda_handler(event, context):
    try:
        # فتح اتصال واحد فقط للـ Batch بالكامل
        db_conn = get_db_connection()
        cursor = db_conn.cursor()
    except Exception as db_err:
        print(f"Database connection failed: {str(db_err)}")
        raise db_err

    # معالجة الرسائل القادمة من طابور SQS
    for record in event['Records']:
        try:
            payload = json.loads(record['body'])
            order_id = payload['order_id']
            user_id = payload['user_id']
            total = payload['total']
            email = payload['email']
            items = payload.get('items', [])
            
            # 1. تسجيل الطلب في جدول orders
            cursor.execute(
                "INSERT INTO orders (id, user_id, total, status) VALUES (%s, %s, %s, %s);",
                (order_id, user_id, total, 'COMPLETED')
            )
            
            # 2. تسجيل عناصر السلة في جدول order_items
            for item in items:
                cursor.execute(
                    "INSERT INTO order_items (order_id, product_id, quantity) VALUES (%s, %s, %s);",
                    (order_id, item['product_id'], item['quantity'])
                )
            
            # حفظ التغييرات للرسالة الحالية
            db_conn.commit()
            print(f"Successfully saved order {order_id} to RDS.")

            # 3. نشر الحدث إلى SNS Topic لإرسال الإشعارات
            sns_message = {
                "order_id": order_id,
                "user_id": user_id,
                "email": email,
                "total": total,
                "status": "COMPLETED"
            }
            
            sns_client.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject=f"Order Confirmed #{order_id[:8]}",
                Message=json.dumps(sns_message)
            )
            print(f"Successfully published order {order_id} to SNS.")
            
        except Exception as e:
            db_conn.rollback()  # إلغاء الحفظ للرسالة الفاشلة فقط لحماية تكامل البيانات
            print(f"Error processing SQS record: {str(e)}")
            raise e
            
    # تنظيف الـ cursor بعد انتهاء الـ Batch
    cursor.close()