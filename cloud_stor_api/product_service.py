import os
import json
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
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
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


# ================= ENV =================
MEDIA_BUCKET_NAME = os.getenv("MEDIA_BUCKET_NAME")
COGNITO_JWKS_URL = os.getenv("COGNITO_JWKS_URL")
COGNITO_CLIENT_ID = os.getenv("COGNITO_CLIENT_ID")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

s3 = boto3.client("s3", region_name=AWS_REGION)


# ================= DB POOL =================
db_pool = None


@app.on_event("startup")
def startup():
    """
    IMPORTANT: lazy safe startup (no circular imports, no early failure)
    """
    global db_pool

    db = get_db_config()

    logger.info(f"Connecting to DB {db['host']}:{db['port']}")

    db_pool = pool.SimpleConnectionPool(
        1, 20,
        host=db["host"],
        database=db["database"],
        user=db["user"],
        password=db["password"],
        port=db.get("port", 5432),
        cursor_factory=RealDictCursor
    )

    logger.info("DB Pool initialized successfully")


# ================= DB CONTEXT =================
@contextmanager
def get_db():
    if db_pool is None:
        raise HTTPException(status_code=500, detail="DB not initialized")

    conn = db_pool.getconn()
    try:
        yield conn
    finally:
        db_pool.putconn(conn)


# ================= JWKS CACHE =================
JWKS_CACHE = {"keys": [], "ts": 0}
JWKS_TTL = 3600


def get_jwks():
    now = time.time()

    if not JWKS_CACHE["keys"] or (now - JWKS_CACHE["ts"]) > JWKS_TTL:
        try:
            if not COGNITO_JWKS_URL:
                return []

            res = requests.get(COGNITO_JWKS_URL, timeout=5)
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

    token = authorization.replace("Bearer ", "")

    try:
        keys = get_jwks()
        if not keys:
            raise HTTPException(status_code=503, detail="Auth unavailable")

        header = jwt.get_unverified_header(token)
        key = next((k for k in keys if k["kid"] == header["kid"]), None)

        if not key:
            raise HTTPException(status_code=401, detail="Invalid key")

        decoded = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=COGNITO_CLIENT_ID
        )

        groups = decoded.get("cognito:groups", [])
        if "Admins" not in groups:
            raise HTTPException(status_code=403, detail="Admin only")

        return decoded

    except Exception as e:
        logger.error(f"Auth error: {e}")
        raise HTTPException(status_code=401, detail="Invalid token")


# ================= DB EXEC =================
def execute(cur, query, params=None):
    cur.execute(query, params)

    if cur.description:
        return cur.fetchall()

    return None


# ================= HEALTH =================
@app.get("/health")
def health():
    return {"status": "ok"}


# ================= PRODUCTS =================
@app.get("/products")
def get_products(limit: int = 20, offset: int = 0):

    with get_db() as conn:
        cur = conn.cursor()

        cur.execute(
            "SELECT id, name, price, image_url FROM products LIMIT %s OFFSET %s",
            (limit, offset)
        )

        return {
            "success": True,
            "data": cur.fetchall()
        }


@app.get("/best-sellers")
def best_sellers():

    with get_db() as conn:
        cur = conn.cursor()

        cur.execute("""
            SELECT id, name, price, image_url
            FROM products
            ORDER BY sales_count DESC
            LIMIT 5
        """)

        return {
            "success": True,
            "data": cur.fetchall()
        }


# ================= S3 UPLOAD =================
@app.post("/admin/products/upload-url")
def upload_url(req: UploadRequest, admin=Depends(verify_admin_token)):

    if not MEDIA_BUCKET_NAME:
        raise HTTPException(status_code=500, detail="Bucket not configured")

    key = f"{uuid.uuid4()}-{req.file_name}"

    url = s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": MEDIA_BUCKET_NAME,
            "Key": key
        },
        ExpiresIn=60
    )

    return {
        "upload_url": url,
        "file_url": f"https://{MEDIA_BUCKET_NAME}.s3.amazonaws.com/{key}"
    }


# ================= CREATE PRODUCT =================
@app.post("/admin/products")
def create_product(product: ProductCreate, admin=Depends(verify_admin_token)):

    with get_db() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO products (name, price, image_url)
            VALUES (%s, %s, %s)
            """,
            (product.name, product.price, product.image_url)
        )

        conn.commit()

        return {"success": True}