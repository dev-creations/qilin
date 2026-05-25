#!/usr/bin/env bash
set -euo pipefail

CERT_DIR="${CERT_DIR:-/certs}"
CERT_FILE="${TLS_CERT_FILE:-${CERT_DIR}/cert.pem}"
KEY_FILE="${TLS_KEY_FILE:-${CERT_DIR}/key.pem}"
HOST="${MCP_HOST:-0.0.0.0}"
TLS_PORT="${MCP_PORT:-8443}"
HTTP_PORT="${MCP_HTTP_PORT:-8080}"
HTTP_ENABLED="${MCP_HTTP_ENABLED:-1}"

mkdir -p "${CERT_DIR}"

if [[ ! -s "${CERT_FILE}" || ! -s "${KEY_FILE}" ]]; then
    echo "[qilin] No TLS cert at ${CERT_FILE}; generating self-signed cert..."
    openssl req -x509 \
        -newkey rsa:4096 \
        -sha256 \
        -days 3650 \
        -nodes \
        -keyout "${KEY_FILE}" \
        -out "${CERT_FILE}" \
        -subj "/C=XX/ST=Local/L=Local/O=Qilin/OU=MCP/CN=qilin" \
        -addext "subjectAltName=DNS:localhost,DNS:qilin,DNS:qilin-mcp,IP:127.0.0.1,IP:0.0.0.0" \
        >/dev/null 2>&1
    chmod 600 "${KEY_FILE}"
    chmod 644 "${CERT_FILE}"
    echo "[qilin] Self-signed cert written to ${CERT_FILE}"
else
    echo "[qilin] Reusing existing TLS cert at ${CERT_FILE}"
fi

echo "[qilin] Starting MCP SSE server (TLS) on https://${HOST}:${TLS_PORT}/sse"
uvicorn qilin.server:app \
    --host "${HOST}" \
    --port "${TLS_PORT}" \
    --ssl-certfile "${CERT_FILE}" \
    --ssl-keyfile "${KEY_FILE}" \
    --proxy-headers \
    --forwarded-allow-ips="*" &
PID_TLS=$!

PID_HTTP=""
if [[ "${HTTP_ENABLED}" == "1" ]]; then
    echo "[qilin] Starting MCP SSE server (plain HTTP) on http://${HOST}:${HTTP_PORT}/sse"
    uvicorn qilin.server:app \
        --host "${HOST}" \
        --port "${HTTP_PORT}" \
        --proxy-headers \
        --forwarded-allow-ips="*" &
    PID_HTTP=$!
fi

shutdown() {
    echo "[qilin] Caught signal, shutting down listeners..."
    [[ -n "${PID_TLS}" ]] && kill -TERM "${PID_TLS}" 2>/dev/null || true
    [[ -n "${PID_HTTP}" ]] && kill -TERM "${PID_HTTP}" 2>/dev/null || true
    wait
    exit 0
}
trap shutdown SIGINT SIGTERM

wait -n
EXIT_CODE=$?
echo "[qilin] A listener exited with code ${EXIT_CODE}; terminating container"
[[ -n "${PID_TLS}" ]] && kill -TERM "${PID_TLS}" 2>/dev/null || true
[[ -n "${PID_HTTP}" ]] && kill -TERM "${PID_HTTP}" 2>/dev/null || true
wait
exit "${EXIT_CODE}"
