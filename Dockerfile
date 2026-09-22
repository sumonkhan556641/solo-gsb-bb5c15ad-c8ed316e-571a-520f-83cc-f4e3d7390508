# syntax=docker/dockerfile:1

# ----------------------------------------------------------------- stage 1: frontend
FROM node:20-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ----------------------------------------------------------------- stage 2: backend
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_PATH=/data/lasercut.db \
    STATIC_DIR=/app/static \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=frontend /build/frontend/dist ./static

RUN mkdir -p /data && \
    adduser --system --uid 1001 appuser && \
    chown -R appuser /data /app
USER appuser

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request; port=os.environ.get('PORT','8000'); r=urllib.request.urlopen('http://127.0.0.1:'+port+'/api/health',timeout=3); raise SystemExit(0 if r.status==200 else 1)"]

CMD ["sh", "-c", "uvicorn app.main:app --host ${HOST} --port ${PORT}"]
