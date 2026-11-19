# Medr web version. Build: docker build -t medr .   Run: see DEPLOY.md
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    MEDR_HOSTED=1 MEDR_DATA=/data PORT=8100
WORKDIR /app
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt
COPY server ./server
RUN useradd -m medr && mkdir -p /data && chown medr /data
USER medr
WORKDIR /app/server
EXPOSE 8100
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips="*"
