# Sebastian on any VM or server: no hosted service, your own machine. See docs/VM.md.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HOME=/data
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install . websockets && useradd -m -u 1000 sebastian && mkdir -p /data && chown sebastian /data
USER sebastian
# config, secrets and the database live in the mounted volume (/data/.config/sebastian), never in the image
VOLUME /data
ENTRYPOINT ["sebastian"]
CMD ["bot"]
