# ================= Base Image =================
FROM python:3.9-slim

# ================= Environment =================
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ================= Workdir =================
WORKDIR /app

# ================= System deps =================
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# ================= Install dependencies =================
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ================= Copy app =================
COPY . .

# ================= Create non-root user (SECURITY BEST PRACTICE) =================
RUN useradd -m appuser
USER appuser

# ================= Expose port =================
EXPOSE 8080

# ================= Healthcheck =================
HEALTHCHECK --interval=30s --timeout=5s \
  CMD curl --fail http://localhost:8080/health || exit 1

# ================= Run production server =================
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "product_service:app", "--bind", "0.0.0.0:8080", "--workers", "2"]