# One isolated Sebastian instance per user: you run your own container and supply your own API key (ADR 0004).
# Build: docker build -t sebastian .   Run: see compose.yaml and docs/QUICKSTART.md
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1 HOME=/data
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install . websockets \
    && useradd --create-home --home-dir /home/sebastian --uid 1000 --shell /usr/sbin/nologin sebastian \
    && mkdir -p /data && chown sebastian /data && chmod 700 /data
USER sebastian
# config, secrets and the database live in the volume (/data/.config/sebastian), never in the image; no key is baked in
VOLUME /data
# healthy while the bot's heartbeat file was touched in the last 5 minutes
HEALTHCHECK --interval=60s --timeout=5s --start-period=90s --retries=3 \
    CMD find /data/.config/sebastian/heartbeat -mmin -5 2>/dev/null | grep -q . || exit 1
ENTRYPOINT ["sebastian"]
CMD ["bot"]
