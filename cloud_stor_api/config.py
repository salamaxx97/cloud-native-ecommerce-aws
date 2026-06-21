import os
import json
import time
import boto3
import logging

logger = logging.getLogger(__name__)

SECRET_CACHE = None
CACHE_TS = 0
CACHE_TTL = 300  # 5 minutes

client = boto3.client(
    "secretsmanager",
    region_name=os.getenv("AWS_REGION", "us-east-1")
)


def get_db_config():
    global SECRET_CACHE, CACHE_TS

    # ================= LOCAL MODE =================
    if os.getenv("ENVIRONMENT", "local") == "local":
        return {
            "host": os.getenv("DB_HOST", "localhost"),
            "database": os.getenv("DB_NAME"),
            "user": os.getenv("DB_USER"),
            "password": os.getenv("DB_PASSWORD"),
            "port": int(os.getenv("DB_PORT", 5432))
        }

    # ================= CACHE =================
    now = time.time()
    if SECRET_CACHE and (now - CACHE_TS) < CACHE_TTL:
        return SECRET_CACHE

    try:
        secret_name = os.environ["DB_SECRET_NAME"]

        response = client.get_secret_value(SecretId=secret_name)
        secret = json.loads(response["SecretString"])

        config = {
            "host": secret["host"],
            "database": secret.get("dbname") or secret.get("database"),
            "user": secret.get("username") or secret.get("user"),
            "password": secret["password"],
            "port": int(secret.get("port", 5432))
        }

        SECRET_CACHE = config
        CACHE_TS = now

        logger.info("DB config loaded successfully")

        return config

    except Exception as e:
        logger.error(f"Failed to load DB secret: {e}")
        raise