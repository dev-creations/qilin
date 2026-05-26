# Recipe: SSE on localhost with a self-signed certificate

**Goal:** connect an MCP client (Cursor, Claude Desktop, etc.) to a local Qilin
over the TLS-terminated SSE endpoint, without disabling certificate verification.

**You'll end up with:** `https://localhost:8443/sse` accepted by the client as
trusted, and `qilin doctor` reporting all green.

## TL;DR

```bash
qilin init        # generates ~/.qilin/certs/{cert,key}.pem (10-year SAN cert)
qilin up
qilin status     # should report qilin-mcp and qdrant as running
```

Then trust the cert system-wide (see below) and point the client at
`https://localhost:8443/sse`.

## Step 1: generate the cert (once)

`qilin init` runs an interactive wizard. The defaults are fine for a local
setup; the relevant ones:

| Prompt | Default | What it controls |
|---|---|---|
| TLS cert path | `~/.qilin/certs/cert.pem` | The cert your client will need to trust. |
| TLS key path | `~/.qilin/certs/key.pem` | Private key. Stays on your machine. |
| Server host | `127.0.0.1` | Loopback only; not reachable from the LAN. |
| Server port | `8443` | TLS listener. |

The cert is valid for 10 years and has SANs for both `localhost` and
`127.0.0.1`, so clients that pin on either should work.

To rotate later:

```bash
qilin cert regenerate
qilin down && qilin up
```

`qilin cert regenerate` refuses to overwrite a cert that wasn't created by
`qilin init` (i.e. user-provided certs are safe).

## Step 2: trust the cert

Trusting the cert system-wide is one command per platform. The cert lives at
`~/.qilin/certs/cert.pem` (run `qilin cert path` if you forgot).

### macOS

```bash
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain \
  ~/.qilin/certs/cert.pem
```

Restart the MCP client afterwards so it re-reads the trust store.

### Linux (Debian/Ubuntu)

```bash
sudo cp ~/.qilin/certs/cert.pem /usr/local/share/ca-certificates/qilin-ca.crt
sudo update-ca-certificates
```

Note the `.crt` extension; `update-ca-certificates` ignores `.pem`.

### Linux (Fedora/RHEL)

```bash
sudo cp ~/.qilin/certs/cert.pem /etc/pki/ca-trust/source/anchors/qilin-ca.crt
sudo update-ca-trust
```

### Windows (PowerShell as Administrator)

```powershell
Import-Certificate `
  -FilePath $env:USERPROFILE\.qilin\certs\cert.pem `
  -CertStoreLocation Cert:\LocalMachine\Root
```

### Per-application (no admin)

If you'd rather not touch the system trust store, most MCP clients let you
point at a CA bundle:

- **Node-based clients** (Claude Desktop, some MCP servers): set
  `NODE_EXTRA_CA_CERTS=$HOME/.qilin/certs/cert.pem` in the client's env.
- **curl-style smoke tests:** `curl --cacert ~/.qilin/certs/cert.pem https://localhost:8443/healthz`.

## Step 3: wire up the client

The minimal `mcpServers` entry:

```json
{
  "mcpServers": {
    "qilin": {
      "url": "https://localhost:8443/sse"
    }
  }
}
```

Drop that into `~/.cursor/mcp.json` or your Claude Desktop config and restart
the client.

## Verifying

```bash
curl --cacert ~/.qilin/certs/cert.pem https://localhost:8443/healthz
# {"ok":true,"version":"1.0.0","qdrant":"ok","embedder":"ok"}

qilin doctor
# checks docker, ollama reachability, qdrant collection access, and the TLS cert
```

If `/healthz` answers but the client still refuses, the most common causes are:

- The client process started before the cert was trusted - restart it.
- The client uses a bundled CA store (Node) and you didn't set
  `NODE_EXTRA_CA_CERTS`.
- The client resolves `localhost` to IPv6 and the cert SAN only covers IPv4;
  the bundled cert covers both, but a *user-provided* cert might not.

## Falling back to plain HTTP

If you trust the loopback boundary and want to skip cert work entirely:

```json
{
  "mcpServers": {
    "qilin": {
      "url": "http://localhost:8080/sse"
    }
  }
}
```

The plain-HTTP listener is bound to `127.0.0.1` only, so it is not reachable
from your LAN. You can disable it entirely with:

```bash
qilin config set server.http_enabled false
qilin down && qilin up
```

## Exposing this off the loopback

Don't. Or rather, don't yet: see the [LAN recipe](expose-on-lan.md) for the
right way to do it once Qilin grows bearer-token auth.
