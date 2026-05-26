# Recipe: Expose Qilin on your LAN

This recipe takes the localhost-only TLS server from
[sse-on-localhost-with-cert.md](sse-on-localhost-with-cert.md) and turns it
into a single-node service the rest of your home lab can reach.

> **Threat model.** Bearer tokens over TLS are fine for a trusted LAN behind
> a firewall. If you put Qilin on a hostile network put it behind a real
> auth proxy (Authelia, oauth2-proxy, Tailscale).

## 1. Pick a hostname and re-issue the cert

Edit `~/.qilin/certs/qilin.cnf` and add the LAN hostname / IP under
`alt_names`:

```ini
[ alt_names ]
DNS.1 = qilin.local
DNS.2 = qilin.lan
IP.1  = 127.0.0.1
IP.2  = 192.168.1.42
```

Regenerate the cert (the steps from the localhost recipe are unchanged; just
point at the new config). Then trust the new cert on every client device
that will talk to the server.

## 2. Set a bearer token

Generate a high-entropy token and write it into your `.env` (or
`docker-compose.override.yml`):

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

```env
QILIN_AUTH_TOKEN=A7xV0lz3...your-token...
```

Equivalent YAML in `~/.qilin/config.json`:

```json
{
  "auth_token": "A7xV0lz3...your-token..."
}
```

For zero-downtime rotation, pass a list. Old clients keep working while you
swap them over to the new token:

```json
{
  "auth_token": ["A7xV0lz3...old...", "B8yWmA4...new..."]
}
```

Restart the container. The startup log should say:

```
INFO qilin.server: Bearer-token auth enabled
```

## 3. Open the port (carefully)

Linux with UFW:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8443 proto tcp
sudo ufw reload
```

macOS: drop a per-port rule into `pf.conf` or run Tailscale's MagicDNS
instead.

## 4. Verify from another machine

```bash
curl --cacert ~/.qilin/certs/qilin.crt \
  -H "Authorization: Bearer ${QILIN_TOKEN}" \
  https://qilin.local:8443/healthz
# {"ok": true, "version": "1.0.0", ...}
```

Without the token:

```bash
curl --cacert ~/.qilin/certs/qilin.crt https://qilin.local:8443/sse
# {"error":"unauthorized","detail":"missing bearer token"}
```

`/healthz` and `/` stay open on purpose so readiness probes / `qilin doctor`
work without credentials.

## 5. Configure your MCP client

Add the `Authorization` header to whatever client config you use. Cursor's
`mcp.json` snippet, for example:

```json
{
  "mcpServers": {
    "qilin": {
      "url": "https://qilin.local:8443/mcp",
      "headers": {
        "Authorization": "Bearer ${env:QILIN_TOKEN}"
      }
    }
  }
}
```

(See [streamable-http](#streamable-http) below for the `/mcp` URL.)

<a id="streamable-http"></a>
## 6. Streamable HTTP transport

Qilin mounts FastMCP's streamable-HTTP app at `/mcp` alongside the existing
`/sse` endpoint when `streamable_http_enabled` is true (default). Newer MCP
clients prefer streamable HTTP because it survives HTTP/2 connection
multiplexing and corporate proxies better than SSE.

Disable it if you only use SSE-aware clients:

```json
{ "streamable_http_enabled": false }
```

## Troubleshooting

- `curl: (60) SSL certificate problem` - the client doesn't trust the cert
  yet; import `~/.qilin/certs/qilin.crt` into the system keychain.
- 401 on every request after a token change - check for stray whitespace
  in your `.env`; the token is compared byte-for-byte.
- `connection refused` from outside but works on localhost - the container
  isn't bound to `0.0.0.0`; set `MCP_HOST=0.0.0.0` (already the default in
  `docker-compose.yml`).

## See also

- [scratch-vs-knowledge-collections](scratch-vs-knowledge-collections.md) for
  TTL'd session memory on the same multi-user box.
- [recall-feedback-loop](recall-feedback-loop.md) for closing the loop on
  what your team actually clicks.
