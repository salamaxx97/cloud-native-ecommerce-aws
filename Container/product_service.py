import os
import time
import uuid
import boto3
import logging
import requests

from jose import jwt
from contextlib import contextmanager
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

from fastapi import FastAPI, HTTPException, Depends, Header, Query
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware

from config import get_db_config


# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("cloud-store-api")


# ================= APP =================
app = FastAPI(title="Cloud Store API", version="1.0.0")


# ================= CORS =================
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),  # local + aws
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ================= MODELS =================
class UploadRequest(BaseModel):
    file_name: str = Field(min_length=3, max_length=255)


class ProductCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    price: float = Field(gt=0)
    image_url: str


# ================= ENV VALIDATION =================
MEDIA_BUCKET_NAME = os.getenv("MEDIA_BUCKET_NAME")
COGNITO_JWKS_URL = os.getenv("COGNITO_JWKS_URL")
COGNITO_CLIENT_ID = os.getenv("COGNITO_CLIENT_ID")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

if not MEDIA_BUCKET_NAME:
    logger.warning("MEDIA_BUCKET_NAME is not set")


# ================= DB CONFIG =================
db = get_db_config()

db_pool = pool.SimpleConnectionPool(
    1,
    20,
    host=db["host"],
    database=db["database"],
    user=db["user"],
    password=db["password"],
    cursor_factory=RealDictCursor
)


@contextmanager
def get_db():
    conn = db_pool.getconn()
    try:
        yield conn
    finally:
        db_pool.putconn(conn)


# ================= AWS CLIENTS =================
s3 = boto3.client("s3", region_name=AWS_REGION)


# ================= JWKS CACHE =================
JWKS_CACHE = {"keys": [], "ts": 0}
JWKS_TTL = 3600


def get_jwks():
    now = time.time()

    if not JWKS_CACHE["keys"] or (now - JWKS_CACHE["ts"]) > JWKS_TTL:
        try:
            if not COGNITO_JWKS_URL:
                return []

            res = requests.get(COGNITO_JWKS_URL, timeout=3)
            JWKS_CACHE["keys"] = res.json().get("keys", [])
            JWKS_CACHE["ts"] = now
        except Exception as e:
            logger.error(f"JWKS fetch failed: {e}")
            JWKS_CACHE["keys"] = []

    return JWKS_CACHE["keys"]


# ================= AUTH =================
def verify_admin_token(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid token format")

    token = authorization.split(" ")[1]

    try:
        keys = get_jwks()

        if not keys:
            raise HTTPException(status_code=503, detail="Auth service unavailable")

        header = jwt.get_unverified_header(token)
        key = next((k for k in keys if k["kid"] == header["kid"]), None)

        if not key:
            raise HTTPException(status_code=401, detail="Invalid signing key")

        decoded = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=COGNITO_CLIENT_ID
        )

        if "Admins" not in decoded.get("cognito:groups", []):
            raise HTTPException(status_code=403, detail="Admin only")

        return decoded

    except jwt.JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception as e:
        logger.error(f"Auth error: {e}")
        raise HTTPException(status_code=500, detail="Authentication failure")


# ================= DB HELPER =================
def execute(cur, query, params=None, retries=3):
    for _ in range(retries):
        try:
            cur.execute(query, params)
            return cur.fetchall() if cur.description else None
        except Exception as e:
            logger.error(f"DB error: {e}")
            time.sleep(0.5)

    raise HTTPException(status_code=500, detail="Database failure")


# ================= HEALTH =================
@app.get("/health")
async def health():
    return {"status": "ok", "service": "cloud-store-api"}


# ================= PRODUCTS =================
@app.get("/products")
async def get_products(limit: int = Query(20), offset: int = Query(0)):
    with get_db() as conn:
        cur = conn.cursor()

        data = execute(
            cur,
            "SELECT id, name, price, image_url FROM products LIMIT %s OFFSET %s",
            (limit, offset)
        )

        return {"success": True, "data": data}


@app.get("/best-sellers")
async def best_sellers():
    with get_db() as conn:
        cur = conn.cursor()

        data = execute(
            cur,
            """
            SELECT id, name, price, image_url
            FROM products
            ORDER BY sales_count DESC
            LIMIT 5
            """
        )

        return {"success": True, "data": data}


# ================= S3 UPLOAD =================
@app.post("/admin/products/upload-url")
async def upload_url(req: UploadRequest, admin_user=Depends(verify_admin_token)):
    if not MEDIA_BUCKET_NAME:
        raise HTTPException(status_code=500, detail="S3 bucket not configured")

    key = f"{uuid.uuid4()}-{req.file_name}"

    upload_url = s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": MEDIA_BUCKET_NAME, "Key": key},
        ExpiresIn=60
    )

    domain = os.getenv(
        "CLOUDFRONT_DOMAIN",
        f"{MEDIA_BUCKET_NAME}.s3.amazonaws.com"
    )

    return {
        "success": True,
        "upload_url": upload_url,
        "file_url": f"https://{domain}/{key}"
    }


# ================= CREATE PRODUCT =================
@app.post("/admin/products")
async def create_product(product: ProductCreate, admin_user=Depends(verify_admin_token)):
    with get_db() as conn:
        cur = conn.cursor()

        try:
            cur.execute(
                """
                INSERT INTO products (name, price, image_url)
                VALUES (%s, %s, %s)
                """,
                (product.name, product.price, product.image_url)
            )

            conn.commit()

            return {"success": True, "message": "Product created"}

        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=str(e))