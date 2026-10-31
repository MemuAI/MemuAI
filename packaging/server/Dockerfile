# MemuAI Server image (server mode)
#
# Builds the MemuAI Server: the existing MemuAI Proxy FastAPI app
# (app/main.py:create_app) plus the /api/server/* router, started by
# memuai_server.py.  The token is generated on first start inside the
# data volume; it is never baked into the image.
#
#   docker build -f packaging/server/Dockerfile -t memuai-server .
#   docker run -d --name memuai-server -p 127.0.0.1:8327:8327 \
#     -v memuai-server-data:/var/lib/memuai-server memuai-server
#
# Serve only behind a reverse proxy with HTTPS (see nginx.conf.example).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    MEMUAI_SERVER_HOST=0.0.0.0 \
    MEMUAI_SERVER_PORT=8327 \
    MEMUAI_SERVER_DATA_DIR=/var/lib/memuai-server

WORKDIR /app

# Full Proxy dependencies (providers, voice, etc.) come from requirements.txt.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY memuai_server.py ./
COPY docs/SERVER.md ./docs/SERVER.md

RUN mkdir -p /var/lib/memuai-server && \
    useradd --system --home-dir /var/lib/memuai-server memuai && \
    chown -R memuai:memuai /var/lib/memuai-server

USER memuai
EXPOSE 8327

# --allow-remote is required for a non-loopback bind; the container is
# expected to sit behind a reverse proxy with HTTPS on the host.
CMD ["python", "memuai_server.py", "--host", "0.0.0.0", "--port", "8327", "--allow-remote"]
