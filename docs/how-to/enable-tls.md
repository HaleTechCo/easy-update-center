# Enable TLS

How to serve the update center over HTTPS, behind your own proxy or from the web container itself.

Signatures already protect the metadata's integrity, so plain HTTP is not unsafe for Jenkins, but
HTTPS is recommended: it protects downloads through proxy mode, and many networks require it.

## Behind a reverse proxy

The web container serves plain HTTP on port 8080 (`HTTP_PORT` on the host). To terminate TLS in a
proxy or ingress you already run:

1. Set `PUBLIC_URL` to the proxy's `https://` origin, for example
   `https://jenkins-updates.example.com`.
2. Forward every path and query string unchanged to the web container's port 8080. The query
   string matters: version routing depends on `?version=`.
3. Publish a release with the new URL: `docker compose run --rm uc generate`.

Redirects always name `PUBLIC_URL`, whatever `Host` header the proxy sends, so no rewriting is
needed in the proxy.

## In the web container

Mount a certificate and key and point `TLS_CERT_FILE` and `TLS_KEY_FILE` at them, in a Compose
override file next to `compose.yaml`:

```yaml
# compose.tls.yaml
services:
  web:
    environment:
      TLS_CERT_FILE: /tls/tls.crt
      TLS_KEY_FILE: /tls/tls.key
    volumes:
      - ./tls:/tls:ro
```

Put the PEM certificate (with its chain) and key in `./tls/`, readable by uid 33 on Linux, then:

```bash
docker compose -f compose.yaml -f compose.tls.yaml up -d
```

Air-gapped, add `-f compose.airgapped.yaml` too, or list all three in `COMPOSE_FILE`. Port 8443
(`HTTPS_PORT` on the host) then serves TLS with Mozilla's "intermediate" settings; 8080 stays
available for a proxy in front. Set `PUBLIC_URL` to the `https://` origin and publish a new release.
