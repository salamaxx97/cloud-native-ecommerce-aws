import os
import json
import boto3

# ================= Cache =================
SECRET_CACHE = None


def get_db_config():
    global SECRET_CACHE

    # ================= Local Environment =================
    if os.getenv("ENVIRONMENT", "local") == "local":
        return {
            "host": os.getenv("DB_HOST"),
            "database": os.getenv("DB_NAME"),
            "user": os.getenv("DB_USER"),
            "password": os.getenv("DB_PASSWORD")
        }

    # ================= AWS Production =================
    if SECRET_CACHE:
        return SECRET_CACHE

    secret_name = os.environ["DB_SECRET_NAME"]

    client = boto3.client(
        "secretsmanager",
        region_name=os.getenv("AWS_REGION", "us-east-1")
    )

    try:
        response = client.get_secret_value(SecretId=secret_name)

        secret = json.loads(response["SecretString"])

        SECRET_CACHE = {
            "host": secret["host"],
            "database": secret.get("dbname") or secret.get("database"),
            "user": secret.get("username") or secret.get("user"),
            "password": secret["password"]
        }

        return SECRET_CACHE

    except Exception as e:
        raise RuntimeError(f"Failed to load DB secret: {str(e)}")