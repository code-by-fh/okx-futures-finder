FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    DASHBOARD_HOST=0.0.0.0 \
    PORT=8765 \
    OKX_CONFIG_FILE=/app/config.json

WORKDIR /app
COPY scanner.py dashboard.py discovery.py market.py ./
COPY dashboard.html dashboard.css dashboard.js ./
# Only the public example configuration is embedded, never the local config.json.
COPY config.example.json ./config.json

USER 10001:10001
EXPOSE 8765
STOPSIGNAL SIGINT
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8765')+'/healthz',timeout=3).read()"]
CMD ["python", "scanner.py"]
