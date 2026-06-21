import boto3
import json
import os
import uuid
import logging

# ================= Logging =================
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ================= AWS =================
sqs = boto3.client("sqs")

QUEUE_URL = os.environ.get("ORDER_QUEUE_URL")


# ================= Response Helper =================
def response(status_code, message, data=None):
    body = {"message": message}
    if data:
        body.update(data)

    return {
        "statusCode": status_code,
        "headers": {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "OPTIONS,POST",
            "Content-Type": "application/json"
        },
        "body": json.dumps(body)
    }


# ================= Lambda Handler =================
def lambda_handler(event, context):
    try:
        logger.info("Checkout event received")

        # ---------------- Validate Queue ----------------
        if not QUEUE_URL:
            logger.error("Missing ORDER_QUEUE_URL")
            return response(500, "Server misconfiguration")

        # ---------------- Parse Body Safely ----------------
        body = {}

        if event.get("body"):
            try:
                body = json.loads(event["body"])
            except json.JSONDecodeError:
                logger.warning("Invalid JSON body received")
                return response(400, "Invalid request body")

        items = body.get("items", [])

        # ---------------- Validation ----------------
        if not items:
            logger.warning("Empty cart checkout attempt")
            return response(400, "Cart is empty")

        # ---------------- User Identity ----------------
        user_id = (
            event.get("requestContext", {})
                  .get("authorizer", {})
                  .get("claims", {})
                  .get("sub", "anonymous")
        )

        # ---------------- Order ID ----------------
        order_id = str(uuid.uuid4())

        # ---------------- SQS Payload ----------------
        sqs_payload = {
            "order_id": order_id,
            "user_id": user_id,
            "items": items,
            "frontend_total": body.get("total", 0)
        }

        # ---------------- Send to SQS ----------------
        sqs.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps(sqs_payload)
        )

        logger.info(f"Order created: {order_id} for user {user_id}")

        return response(
            200,
            "Order received successfully",
            {"order_id": order_id}
        )

    except Exception as e:
        logger.error(f"Unhandled error: {str(e)}")
        return response(500, "Internal server error")