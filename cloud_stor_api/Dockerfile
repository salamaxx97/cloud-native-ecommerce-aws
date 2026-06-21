FROM python:3.9-slim

# ================= Environment =================
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ================= Workdir =================
WORKDIR /app

# ================= System dependencies =================
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ================= Install Python deps =================
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ================= Copy source =================
COPY . .

# ================= Create non-root user =================
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# ================= Expose =================
EXPOSE 8080

# ================= Healthcheck =================
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

# ================= Run app =================
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "product_service:app", "--bind", "0.0.0.0:8080", "--workers", "2"]