# Local TLS certificates for HTTPS

When using OIDC (e.g. IBM Verify) with redirect URIs like `https://localhost:7860/manage`, the server must run over HTTPS.

Generate a self-signed certificate (one-time):

```bash
openssl req -x509 -newkey rsa:4096 -keyout localhost.key -out localhost.crt \
  -days 365 -nodes -subj "/CN=localhost"
```

Then set in `.env`:

- `SSL_KEYFILE="deployment/certs/localhost.key"`
- `SSL_CERTFILE="deployment/certs/localhost.crt"`

Start the demo server with `cuga start demo`; it will use TLS when these env vars are set.
