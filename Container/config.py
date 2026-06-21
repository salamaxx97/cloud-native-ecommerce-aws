import os
import json
import boto3

def get_db_config():

    # Local Development
    if os.getenv("ENVIRONMENT", "local") == "local":
        return {
            "host": os.getenv("DB_HOST"),
            "database": os.getenv("DB_NAME"),
            "user": os.getenv("DB_USER"),
            "password": os.getenv("DB_PASSWORD")
        }

    # AWS Production
    secret_name = os.environ["DB_SECRET_NAME"]

    client = boto3.client("secretsmanager")

    response = client.get_secret_value(
        SecretId=secret_name
    )

    secret = json.loads(response["SecretString"])

    return {
        "host": secret["host"],
        "database": secret["dbname"],
        "user": secret["username"],
        "password": secret["password"]
    }