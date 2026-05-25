FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssl ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip \
    && pip install .

COPY scripts ./scripts
RUN sed -i 's/\r$//' /app/scripts/entrypoint.sh \
    && chmod +x /app/scripts/entrypoint.sh

RUN mkdir -p /certs
VOLUME ["/certs"]

EXPOSE 8443

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsk https://127.0.0.1:8443/healthz || exit 1

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
