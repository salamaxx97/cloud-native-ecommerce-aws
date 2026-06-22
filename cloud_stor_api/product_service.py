import os
import json
import time
import uuid
import boto3
import logging
import requests

from jose import jwt
from jose import jwk
from jose.utils import base64url_decode

from contextlib import contextmanager
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware

from config import get_db_config


# ================= LOGGING =================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cloud-store-api")


# ================= APP =================
app = FastAPI(title="Cloud Store API", version="1.0.0")


# ================= ENV =================
MEDIA_BUCKET_NAME = os.getenv("MEDIA_BUCKET_NAME")
COGNITO_JWKS_URL = os.getenv("COGNITO_JWKS_URL")
COGNITO_CLIENT_ID = os.getenv("COGNITO_CLIENT_ID")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
COGNITO_DOMAIN = os.getenv("COGNITO_DOMAIN")


# ================= CORS =================
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ================= AWS =================
s3 = boto3.client("s3", region_name=AWS_REGION)


# ================= MODELS =================
class UploadRequest(BaseModel):
    file_name: str

class ProductCreate(BaseModel):
    name: str
    price: float
    image_url: str

class ProductUpdate(BaseModel):
    name: str
    price: float
    image_url: str

# ================= DB =================
db_pool = None

@app.on_event("startup")
def startup():
    global db_pool
    db = get_db_config()

    db_pool = pool.SimpleConnectionPool(
        1, 20,
        host=db["host"],
        database=db["database"],
        user=db["user"],
        password=db["password"],
        port=db.get("port", 5432),
        cursor_factory=RealDictCursor
    )

    logger.info("DB connected")


@contextmanager
def get_db():
    conn = db_pool.getconn()
    try:
        yield conn
    finally:
        db_pool.putconn(conn)


# ================= JWKS =================
JWKS_CACHE = {"keys": [], "ts": 0}
JWKS_TTL = 3600


def get_jwks():
    now = time.time()

    if not JWKS_CACHE["keys"] or now - JWKS_CACHE["ts"] > JWKS_TTL:
        res = requests.get(COGNITO_JWKS_URL, timeout=5)
        JWKS_CACHE["keys"] = res.json()["keys"]
        JWKS_CACHE["ts"] = now

    return JWKS_CACHE["keys"]


# ================= AUTH FIXED =================
def verify_admin_token(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")

    token = authorization.replace("Bearer ", "")

    try:
        keys = get_jwks()
        header = jwt.get_unverified_header(token)

        key = next((k for k in keys if k["kid"] == header["kid"]), None)
        if not key:
            raise HTTPException(status_code=401, detail="Invalid key")

        public_key = jwk.construct(key)

        message, encoded_sig = token.rsplit(".", 1)
        decoded_sig = base64url_decode(encoded_sig.encode())

        if not public_key.verify(message.encode(), decoded_sig):
            raise HTTPException(status_code=401, detail="Bad signature")

        claims = jwt.get_unverified_claims(token)

        # 🔥 FIX 1: check token type
        if claims.get("token_use") not in ["id", "access"]:
            raise HTTPException(status_code=401, detail="Invalid token_use")

        # 🔥 FIX 2: audience check ONLY for id token
        if claims.get("token_use") == "id":
            if claims.get("aud") != COGNITO_CLIENT_ID:
                raise HTTPException(status_code=401, detail="Bad audience")

        # 🔥 FIX 3: access token uses client_id instead
        if claims.get("token_use") == "access":
            if claims.get("client_id") != COGNITO_CLIENT_ID:
                raise HTTPException(status_code=401, detail="Bad client_id")

        # 🔥 ADMIN CHECK
        groups = claims.get("cognito:groups", [])
        if "Admins" not in groups:
            raise HTTPException(status_code=403, detail="Admins only")

        return claims

    except Exception as e:
        logger.error(e)
        raise HTTPException(status_code=401, detail="Unauthorized")


# ================= HEALTH =================
@app.get("/health")
def health():
    return {"status": "ok"}


# ================= PRODUCTS =================
@app.get("/products")
def products():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM products")
        return {"data": cur.fetchall()}


@app.get("/best-sellers")
def best():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM products
            ORDER BY sales_count DESC
            LIMIT 5
        """)
        return {"data": cur.fetchall()}


# ================= S3 UPLOAD =================
@app.post("/admin/products/upload-url")
def upload_url(req: UploadRequest, admin=Depends(verify_admin_token)):
    key = f"{uuid.uuid4()}-{req.file_name}"
    
    # 1. حدد الـ ContentType هنا صراحةً
    # 2. لو الـ file_name ملوش extension ثابت، ممكن تمرره من الـ request body
    content_type = "image/webp" # أو ديناميكياً بناءً على الـ file_name
    
    url = s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": MEDIA_BUCKET_NAME,
            "Key": key,
            "ContentType": content_type # 👈 ده هو "القفل" اللي بيحميك
        },
        ExpiresIn=300
    )
    return {"upload_url": url, "file_url": f"https://{MEDIA_BUCKET_NAME}.s3.amazonaws.com/{key}"}
# ================= CREATE PRODUCT =================
@app.post("/admin/products")
def create(product: ProductCreate, admin=Depends(verify_admin_token)):

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO products (name, price, image_url) VALUES (%s,%s,%s)",
            (product.name, product.price, product.image_url)
        )
        conn.commit()

    return {"success": True}

    # ================= ْUpdate PRODUCT =================
@app.put("/admin/products/{product_id}")
def update_product(product_id: int, product: ProductUpdate, admin=Depends(verify_admin_token)):
    with get_db() as conn:
        cur = conn.cursor()

        cur.execute("""
            UPDATE products
            SET name=%s, price=%s, image_url=%s
            WHERE id=%s
        """, (
            product.name,
            product.price,
            product.image_url,
            product_id
        ))

        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Product not found")

        conn.commit()

    return {"success": True, "message": "Product updated"}

from urllib.parse import urlparse

@app.delete("/admin/products/{product_id}")
def delete_product(product_id: int, admin=Depends(verify_admin_token)):
    with get_db() as conn:
        cur = conn.cursor()

        cur.execute("SELECT image_url FROM products WHERE id=%s", (product_id,))
        product = cur.fetchone()

        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        image_url = product["image_url"]

        if image_url:
            try:
                parsed = urlparse(image_url)
                key = parsed.path.lstrip("/")

                s3.delete_object(
                    Bucket=MEDIA_BUCKET_NAME,
                    Key=key
                )
            except Exception as e:
                logger.warning(f"S3 delete failed: {e}")

        cur.execute("DELETE FROM products WHERE id=%s", (product_id,))

        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Product not found")

        conn.commit()

    return {"success": True, "message": "Product deleted"}