# ================= LAMBDA HANDLER =================
def lambda_handler(event, context):
    db_conn = None
    cursor = None

    try:
        db_conn = get_db_connection()
        cursor = db_conn.cursor()

        # ================= PROCESS SQS BATCH =================
        for record in event.get('Records', []):
            try:
                payload = json.loads(record['body'])

                order_id = payload['order_id']
                user_id = payload['user_id']
                total = payload['total']
                email = payload['email']
                items = payload.get('items', [])

                # ================= INSERT ORDER =================
                cursor.execute(
                    """
                    INSERT INTO orders (id, user_id, total, status)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (order_id, user_id, total, 'COMPLETED')
                )

                # ================= INSERT ITEMS =================
                for item in items:
                    cursor.execute(
                        """
                        INSERT INTO order_items (order_id, product_id, quantity)
                        VALUES (%s, %s, %s)
                        """,
                        (order_id, item['product_id'], item['quantity'])
                    )

                # حفظ التغييرات للرسالة الحالية بنجاح
                db_conn.commit()
                print(f"Order saved successfully: {order_id}")

                # ================= SNS NOTIFICATION =================
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
                print(f"SNS sent for order: {order_id}")

            except Exception as e:
                db_conn.rollback() # إلغاء حركات هذه الرسالة الفاشلة فقط
                print(f"Failed processing record: {str(e)}")
                raise # تعيد إطلاق الخطأ لإعلام SQS بفشل الـ Batch

    finally:
        # ================= CLEANUP (Safe & Optimized) =================
        if cursor:
            try:
                cursor.close()
                print("Cursor closed successfully.")
            except Exception as e:
                print(f"Error closing cursor: {str(e)}")
        
