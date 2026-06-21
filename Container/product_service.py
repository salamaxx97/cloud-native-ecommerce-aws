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

from config import get_db_config
from fastapi.middleware.cors import CORSMiddleware

# ================= Logging =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("cloud-store-api")

app = FastAPI(title="Cloud Store API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # React فقط
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= Models =================
class UploadRequest(BaseModel):
    file_name: str = Field(min_length=3, max_length=255)

class ProductCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    price: float = Field(gt=0)
    image_url: str


# ================= DB Config =================
db = get_db_config()

# ================= DB Pool =================
db_pool = pool.SimpleConnectionPool(
    minconn=1,
    maxconn=20,
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


# ================= AWS =================
s3 = boto3.client("s3")


# ================= JWKS CACHE (FIXED SAFE MODE) =================
JWKS_CACHE = {"keys": [], "ts": 0}
JWKS_TTL = 3600

COGNITO_JWKS_URL = os.environ.get("COGNITO_JWKS_URL")
COGNITO_CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID")


def get_jwks():
    now = time.time()

    # refresh logic
    if not JWKS_CACHE["keys"] or (now - JWKS_CACHE["ts"]) > JWKS_TTL:
        try:
            res = requests.get(COGNITO_JWKS_URL, timeout=3)
            JWKS_CACHE["keys"] = res.json().get("keys", [])
            JWKS_CACHE["ts"] = now
        except Exception as e:
            logger.error(f"JWKS fetch failed: {e}")
            # مهم: fallback لا يكسر النظام
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
            logger.warning(f"Unauthorized access attempt: {decoded.get('username')}")
            raise HTTPException(status_code=403, detail="Admin only")

        return decoded

    except jwt.JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception as e:
        logger.error(f"Auth error: {e}")
        raise HTTPException(status_code=500, detail="Authentication failure")


# ================= DB HELPER =================
def execute(cur, query, params=None, retries=3):
    for i in range(retries):
        try:
            cur.execute(query, params)
            return cur.fetchall() if cur.description else None
        except Exception as e:
            logger.error(f"DB attempt {i+1} failed: {e}")
            time.sleep(0.5)

    raise HTTPException(status_code=500, detail="Database failure")


# ================= HEALTH =================
@app.get("/health")
async def health():
    return {"status": "ok", "service": "cloud-store-api"}


# ================= PUBLIC APIs =================
@app.get("/products")
async def get_products(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    with get_db() as conn:
        cur = conn.cursor()

        data = execute(
            cur,
            "SELECT id, name, price, image_url FROM products LIMIT %s OFFSET %s",
            (limit, offset)
        )

        return {
            "success": True,
            "data": data,
            "meta": {"limit": limit, "offset": offset}
        }


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


# ================= ADMIN APIs =================
@app.post("/admin/products/upload-url")
async def upload_url(
    req: UploadRequest,
    admin_user=Depends(verify_admin_token)
):
    bucket = os.environ["MEDIA_BUCKET_NAME"]
    safe_key = f"{uuid.uuid4()}-{req.file_name}"

    try:
        upload_url = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": safe_key},
            ExpiresIn=60
        )

        cloudfront = os.environ.get(
            "CLOUDFRONT_DOMAIN",
            f"{bucket}.s3.amazonaws.com"
        )

        return {
            "success": True,
            "upload_url": upload_url,
            "file_url": f"https://{cloudfront}/{safe_key}"
        }

    except Exception as e:
        logger.error(f"S3 error: {e}")
        raise HTTPException(status_code=500, detail="Upload failed")


@app.post("/admin/products")
async def create_product(
    product: ProductCreate,
    admin_user=Depends(verify_admin_token)
):
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

            logger.info(f"Product created: {product.name}")

            return {"success": True, "message": "Product created"}

        except Exception as e:
            conn.rollback()
            logger.error(f"DB error: {e}")

            raise HTTPException(
                status_code=500,
                detail={
                    "error": "DB_ERROR",
                    "message": "Failed to create product"
                }
            )